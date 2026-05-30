# Expected Output — What spec-editor generates from bookstore input

After running `spec-editor run`, the agents produce a structured specification
with these elements. Actual output may vary — agents make independent decisions.

## Specification Summary

| Aspect | Elements | Key IDs |
|--------|----------|---------|
| modules | 5 | MOD-001 (Catalog), MOD-002 (Cart), MOD-003 (Checkout), MOD-004 (Accounts), MOD-005 (Admin) |
| user_scenarios | 3 epics → 8 user stories → 12 acceptance criteria | US-001 (Browse), US-003 (Checkout), US-007 (Admin Dashboard) |
| data_entities | 4 | ENT-001 (Book), ENT-002 (Order), ENT-003 (User), ENT-004 (CartItem) |
| non_functional | 4 | NFR-001 (2s page load), NFR-002 (1000 concurrent), NFR-003 (PCI-DSS), NFR-004 (GDPR) |

## Sample Spec Element

```yaml
---
aspect: modules
element_type: module
id: MOD-001
title: Book Catalog
status: reviewed
parent: null
children: [MOD-001-C1, MOD-001-C2]
relationships:
  depends_on: [{role: depends_on, target: MOD-004}]
derived_from: [SRC-001]
tags: [catalog, search, p0]
provenance:
  source: input.md
  confidence: 0.9
---
Handles book browsing, search by title/author, category navigation,
and book detail pages with cover images, descriptions, pricing,
and stock availability.
```

## Traceability Matrix

| Requirement | Spec Element | Code |
|------------|-------------|------|
| "Browse books by category" | MOD-001 (Book Catalog) | `catalog/service.py @implements("MOD-001")` |
| "Shopping cart" | MOD-002 (Shopping Cart) | `cart/service.py @implements("MOD-002")` |
| "Checkout with credit card" | MOD-003 (Checkout) | `checkout/service.py @implements("MOD-003")` |
| "User registration" | MOD-004 (User Accounts) | `auth/service.py @implements("MOD-004")` |
| "Admin dashboard" | MOD-005 (Admin Panel) | `admin/service.py @implements("MOD-005")` |
| "2 second page load" | NFR-001 (Performance) | `middleware/cache.py @implements("NFR-001")` |
| "GDPR deletion" | NFR-004 (GDPR) | `accounts/gdpr.py @implements("NFR-004")` |

## Verify Traceability Output

```
$ spec-editor verify-traceability -p . -c ./src -l python

Total requirements: 15
Implemented: 12
Coverage: 80.0%

Gaps:
  ⚠ NFR-002 "1000 concurrent users" — no @implements in code
  ⚠ MOD-001-C2 "Search Engine" — no @implements in code
  ⚠ MOD-004-C3 "Password Reset" — no @implements in code
```
