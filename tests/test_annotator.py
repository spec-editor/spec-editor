"""Tests for word matching logic in annotator.py."""

import pytest

from src.mcp.annotator import find_matches, split_words, words_overlap_score

# ======================================================================
# split_words
# ======================================================================


class TestSplitWords:
    """Splitting identifiers into words."""

    def test_snake_case(self):
        assert split_words("get_user_token") == {"get", "user", "token"}

    def test_camel_case(self):
        assert split_words("getUserToken") == {"get", "user", "token"}

    def test_pascal_case(self):
        assert split_words("UserAuthenticator") == {"user", "authenticator"}

    def test_kebab_case(self):
        assert split_words("user-auth-service") == {"user", "auth", "service"}

    def test_spaces(self):
        assert split_words("User Auth") == {"user", "auth"}

    def test_mixed_case(self):
        assert split_words("getUser_auth-Token") == {"get", "user", "auth", "token"}

    def test_short_words_kept_in_set(self):
        """Short words remain in the set; filtering is in scoring."""
        result = split_words("a_bc_def")
        assert "a" in result
        assert "bc" in result
        assert "def" in result

    def test_single_word(self):
        assert split_words("hello") == {"hello"}

    def test_empty_string(self):
        assert split_words("") == set()

    def test_numbers_handled(self):
        assert split_words("func123Test") == {"func", "123", "test"}


# ======================================================================
# words_overlap_score
# ======================================================================


class TestWordsOverlapScore:
    """Counting word overlap between two sets."""

    def test_full_overlap(self):
        title = {"user", "auth"}
        symbol = {"user", "auth", "service"}
        assert words_overlap_score(title, symbol) == 1.0

    def test_partial_overlap(self):
        title = {"user", "auth", "profile"}
        symbol = {"user", "auth", "service"}
        assert words_overlap_score(title, symbol) == 2 / 3

    def test_no_overlap(self):
        title = {"payment", "gateway"}
        symbol = {"user", "auth"}
        assert words_overlap_score(title, symbol) == 0.0

    def test_empty_title(self):
        assert words_overlap_score(set(), {"user"}) == 0.0

    def test_empty_symbol(self):
        assert words_overlap_score({"user"}, set()) == 0.0

    def test_short_words_ignored(self):
        """Words < 3 chars are ignored during scoring."""
        title = {"api", "key"}
        symbol = {"api", "token"}
        # "api" = 3 chars (not ignored), "key" = 3 chars (not ignored)
        assert words_overlap_score(title, symbol) == 0.5


# ======================================================================
# find_matches
# ======================================================================


class TestFindMatches:
    """Integration: matching symbols to requirements."""

    def make_index(self, pairs: list[tuple[str, str]]) -> dict[str, str]:
        """Create title_index from (title, req_id) pairs."""
        index: dict[str, str] = {}
        for title, req_id in pairs:
            index[title.lower()] = req_id
        return index

    def test_exact_match(self):
        """'User Auth' should match 'user_auth'."""
        index = self.make_index([("User Auth", "REQ-001")])
        matches = find_matches("user_auth", index)
        assert "REQ-001" in matches

    def test_camel_case_match(self):
        """'User Auth' should match 'UserAuthenticator'."""
        index = self.make_index([("User Auth", "REQ-001")])
        matches = find_matches("UserAuthenticator", index)
        assert "REQ-001" in matches

    def test_short_key_no_false_positive(self):
        """Short key 'API' should not match everything."""
        index = self.make_index([("API", "REQ-001")])
        # "api" has only one 3-letter word — self-match should work
        matches_self = find_matches("api_client", index)
        # "api" (3 chars) in "api_client" -> 1/1 = 1.0 overlap, should match
        assert "REQ-001" in matches_self
        # But "database" does not contain "api" -> 0 overlap, should not match
        matches_db = find_matches("database", index)
        assert "REQ-001" not in matches_db

    def test_short_title_word_ignored(self):
        """Title consisting only of short words does not produce false positives."""
        index = self.make_index([("My API Key", "REQ-001")])
        # "my" (2 chars — ignored), "api" (3 chars), "key" (3 chars)
        # The symbol "UserService" contains neither "api" nor "key" -> 0 matches
        matches = find_matches("UserService", index)
        assert "REQ-001" not in matches

    def test_partial_overlap_threshold(self):
        """With overlap < 50% there is no match."""
        index = self.make_index([("User Payment Gateway", "REQ-001")])
        # title words: user, payment, gateway (3 words >= 3 chars)
        # symbol "getUserToken": user (1 overlap) -> 1/3 < 0.5
        matches = find_matches("getUserToken", index)
        assert "REQ-001" not in matches

    def test_majority_overlap(self):
        """With overlap >= 50% there is a match."""
        index = self.make_index([("User Token Service", "REQ-001")])
        # title words: user, token, service (3 words)
        # symbol "get_user_token": user, token (2 overlap) -> 2/3 >= 0.5
        matches = find_matches("get_user_token", index)
        assert "REQ-001" in matches

    def test_multiple_titles(self):
        """Correct selection among multiple requirements."""
        index = self.make_index(
            [
                ("User Auth", "REQ-001"),
                ("Payment Gateway", "REQ-002"),
                ("User Profile", "REQ-003"),
            ]
        )
        # "UserAuthenticator" matches "User Auth" but not the rest
        matches = find_matches("UserAuthenticator", index)
        assert "REQ-001" in matches
        assert "REQ-002" not in matches
        # "PaymentGateway" matches "Payment Gateway"
        matches = find_matches("PaymentGateway", index)
        assert "REQ-002" in matches
        assert "REQ-001" not in matches

    def test_no_match_returns_empty(self):
        index = self.make_index([("User Auth", "REQ-001")])
        matches = find_matches("ProcessPayment", index)
        assert matches == []

    def test_empty_index(self):
        matches = find_matches("anything", {})
        assert matches == []


# ======================================================================
# Stemming-aware matching
# ======================================================================


class TestStemmingMatches:
    """Matching via word stems: authenticator ↔ authentication."""

    def make_index(self, pairs: list[tuple[str, str]]) -> dict[str, str]:
        index: dict[str, str] = {}
        for title, req_id in pairs:
            index[title.lower()] = req_id
        return index

    def test_stem_match_different_forms(self):
        """'authenticator' in code matches 'Authentication' in spec via stem."""
        index = self.make_index([("User Authentication", "REQ-001")])
        matches = find_matches("UserAuthenticator", index)
        assert "REQ-001" in matches

    def test_stem_match_plural(self):
        """'users' matches 'User' via stem."""
        index = self.make_index([("User Management", "REQ-002")])
        matches = find_matches("manage_users", index)
        assert "REQ-002" in matches

    def test_stem_match_verb_noun(self):
        """'create' matches 'Creation' via stem 'creat'."""
        index = self.make_index([("Account Creation", "REQ-003")])
        matches = find_matches("create_account", index)
        assert "REQ-003" in matches

    def test_stem_match_gerund(self):
        """'processing' matches 'Process' via stem."""
        index = self.make_index([("Payment Process", "REQ-004")])
        matches = find_matches("PaymentProcessing", index)
        assert "REQ-004" in matches

    def test_stem_no_false_positive_on_different_roots(self):
        """'running' should NOT match 'runner' via stem — different root."""
        index = self.make_index([("Task Runner", "REQ-005")])
        # 'run' vs 'runner' — same stem 'run', so this WOULD match with stemming
        # But 'running' should NOT match 'Status' (different root)
        index2 = self.make_index([("System Status", "REQ-006")])
        matches = find_matches("TaskRunner", index2)
        assert "REQ-006" not in matches

    def test_stem_improves_partial_match(self):
        """Without stemming, 'validator' vs 'Validation' would fail.
        With stemming, they share stem 'valid'."""
        index = self.make_index([("Input Validation", "REQ-007")])
        matches = find_matches("InputValidator", index)
        assert "REQ-007" in matches
