"""Tests for multi-language code parsers: Go, Java, Kotlin, Rust.

Each parser extracts @implements annotations (from decorators/annotations
and comments) and identifies code symbols (classes, functions, methods).
"""

import tempfile
from pathlib import Path

import pytest


def _write_code(path: Path, code: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


# ======================================================================
# Go parser
# ======================================================================


class TestParseGo:
    """Go parser: comment-based @implements detection."""

    def parse(self, code: str) -> tuple:
        from src.mcp.parsers.go import parse_go

        with tempfile.NamedTemporaryFile(suffix=".go", mode="w", delete=False) as f:
            f.write(code)
            f.flush()
            return parse_go(Path(f.name))

    def test_function_with_comment(self):
        code = """package main

// @implements("REQ-001")
func CreateUser(name string) error {
    return nil
}
"""
        annotations, symbols = self.parse(code)
        assert len(annotations) == 1
        assert annotations[0].req_id == "REQ-001"
        assert annotations[0].symbol == "CreateUser"
        assert any(s.name == "CreateUser" for s in symbols)

    def test_method_with_comment(self):
        code = """package main

type UserService struct{}

// @implements("REQ-002")
func (s *UserService) GetUser(id int) string {
    return ""
}
"""
        annotations, symbols = self.parse(code)
        assert len(annotations) == 1
        assert annotations[0].req_id == "REQ-002"
        assert annotations[0].symbol == "GetUser"

    def test_struct_type_with_comment(self):
        code = """package models

// @implements("ENT-001")
type User struct {
    ID   int
    Name string
}
"""
        annotations, symbols = self.parse(code)
        assert len(annotations) >= 1
        assert any(a.req_id == "ENT-001" for a in annotations)

    def test_no_implements_returns_empty(self):
        code = """package main

func Helper() {}
"""
        annotations, symbols = self.parse(code)
        assert annotations == []

    def test_symbols_collected(self):
        code = """package main

func Func1() {}
func Func2() {}

type MyStruct struct{}
"""
        _, symbols = self.parse(code)
        names = {s.name for s in symbols}
        assert "Func1" in names
        assert "Func2" in names
        assert "MyStruct" in names


# ======================================================================
# Java parser
# ======================================================================


class TestParseJava:
    """Java parser: annotation + comment-based @implements."""

    def parse(self, code: str) -> tuple:
        from src.mcp.parsers.java import parse_java

        with tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False) as f:
            f.write(code)
            f.flush()
            return parse_java(Path(f.name))

    def test_class_with_annotation(self):
        code = """public class UserService {
    @Implements("REQ-001")
    public void createUser() {
    }
}
"""
        annotations, symbols = self.parse(code)
        assert len(annotations) == 1
        assert annotations[0].req_id == "REQ-001"
        assert annotations[0].symbol == "createUser"

    def test_method_with_comment(self):
        code = """public class AuthService {
    // @implements("REQ-002")
    public boolean authenticate(String token) {
        return true;
    }
}
"""
        annotations, symbols = self.parse(code)
        assert any(a.req_id == "REQ-002" for a in annotations)

    def test_interface_detected(self):
        code = """// @implements("API-001")
public interface PaymentGateway {
    void processPayment();
}
"""
        annotations, symbols = self.parse(code)
        assert any(s.kind == "class" and s.name == "PaymentGateway" for s in symbols)

    def test_no_annotations_returns_empty(self):
        code = """public class Helper {
    public void doWork() {}
}
"""
        annotations, symbols = self.parse(code)
        assert annotations == []

    def test_multiple_classes(self):
        code = """// @implements("MOD-001")
public class UserModule {}

// @implements("MOD-002")
public class PaymentModule {}
"""
        _, symbols = self.parse(code)
        names = {s.name for s in symbols}
        assert "UserModule" in names
        assert "PaymentModule" in names


# ======================================================================
# Kotlin parser
# ======================================================================


class TestParseKotlin:
    """Kotlin parser: annotation + comment-based @implements."""

    def parse(self, code: str) -> tuple:
        from src.mcp.parsers.kotlin import parse_kotlin

        with tempfile.NamedTemporaryFile(suffix=".kt", mode="w", delete=False) as f:
            f.write(code)
            f.flush()
            return parse_kotlin(Path(f.name))

    def test_function_with_comment(self):
        code = """package com.example

// @implements("REQ-001")
fun createUser(name: String): User {
    return User(name)
}
"""
        annotations, symbols = self.parse(code)
        assert len(annotations) == 1
        assert annotations[0].req_id == "REQ-001"
        assert annotations[0].symbol == "createUser"

    def test_class_with_annotation(self):
        code = """package com.example

@Implements("REQ-002")
class UserService {
    fun getUser(id: Int): User = User("")
}
"""
        annotations, symbols = self.parse(code)
        # Annotation on class should be found
        assert any(a.req_id == "REQ-002" for a in annotations)

    def test_data_class_with_comment(self):
        code = """// @implements("ENT-001")
data class User(
    val id: Int,
    val name: String,
    val email: String
)
"""
        annotations, symbols = self.parse(code)
        assert any(a.req_id == "ENT-001" for a in annotations)

    def test_object_declaration(self):
        code = """// @implements("SRV-001")
object UserRepository {
    fun findById(id: Int): User? = null
}
"""
        _, symbols = self.parse(code)
        assert any(s.name == "UserRepository" for s in symbols)

    def test_no_annotations_empty(self):
        code = """fun helper() = Unit"""
        annotations, _ = self.parse(code)
        assert annotations == []


# ======================================================================
# Rust parser
# ======================================================================


class TestParseRust:
    """Rust parser: comment-based @implements detection."""

    def parse(self, code: str) -> tuple:
        from src.mcp.parsers.rust import parse_rust

        with tempfile.NamedTemporaryFile(suffix=".rs", mode="w", delete=False) as f:
            f.write(code)
            f.flush()
            return parse_rust(Path(f.name))

    def test_function_with_comment(self):
        code = """// @implements("REQ-001")
fn create_user(name: &str) -> Result<User, Error> {
    Ok(User { name: name.to_string() })
}
"""
        annotations, symbols = self.parse(code)
        assert len(annotations) == 1
        assert annotations[0].req_id == "REQ-001"
        assert annotations[0].symbol == "create_user"

    def test_struct_with_comment(self):
        code = """// @implements("ENT-001")
pub struct User {
    pub id: i64,
    pub name: String,
}
"""
        annotations, symbols = self.parse(code)
        assert any(a.req_id == "ENT-001" for a in annotations)

    def test_impl_block_with_comment(self):
        code = """// @implements("MOD-001")
impl User {
    pub fn new(name: &str) -> Self {
        User { id: 0, name: name.to_string() }
    }
}
"""
        annotations, symbols = self.parse(code)
        assert any(a.req_id == "MOD-001" for a in annotations)

    def test_trait_with_comment(self):
        code = """// @implements("API-001")
pub trait Repository {
    fn find_by_id(&self, id: i64) -> Option<Entity>;
}
"""
        _, symbols = self.parse(code)
        assert any(s.name == "Repository" for s in symbols)

    def test_enum_with_comment(self):
        code = """// @implements("ENM-001")
pub enum Status {
    Active,
    Inactive,
    Pending,
}
"""
        _, symbols = self.parse(code)
        assert any(s.name == "Status" for s in symbols)

    def test_no_annotations_returns_empty(self):
        code = """fn helper() -> i32 { 42 }"""
        annotations, _ = self.parse(code)
        assert annotations == []
