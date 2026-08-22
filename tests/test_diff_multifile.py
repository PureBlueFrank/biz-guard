"""Behavior contract for the safe multi-file unified-diff parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import EvaluationRequest
from bizguard.diff_parser import DiffParseError, parse_unified


TWO_JAVA_FILES = """\
diff --git a/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java b/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java
--- a/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java
+++ b/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java
@@ -1,3 +1,3 @@
 public class CouponResponse {
-    private String status;
+    private String statusCode;
 }
diff --git a/merchant-service/src/main/java/com/bizguard/merchant/api/MerchantController.java b/merchant-service/src/main/java/com/bizguard/merchant/api/MerchantController.java
--- a/merchant-service/src/main/java/com/bizguard/merchant/api/MerchantController.java
+++ b/merchant-service/src/main/java/com/bizguard/merchant/api/MerchantController.java
@@ -1,3 +1,3 @@
 public class MerchantController {
-    public String ping() { return "pong"; }
+    public String ping() { return "ok"; }
 }
"""

DTO_AND_DECOY = """\
diff --git a/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java b/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java
--- a/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java
+++ b/coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java
@@ -1,5 +1,4 @@
 public class CouponResponse {
     private String couponCode;
     private String status;
-    private String removedField;
 }
diff --git a/coupon-core/src/main/java/com/bizguard/coupon/util/Unrelated.java b/coupon-core/src/main/java/com/bizguard/coupon/util/Unrelated.java
--- a/coupon-core/src/main/java/com/bizguard/coupon/util/Unrelated.java
+++ b/coupon-core/src/main/java/com/bizguard/coupon/util/Unrelated.java
@@ -1,3 +1,3 @@
 public class Unrelated {
-    // decoy
+    // decoy changed
 }
"""

ADD_FILE = """\
diff --git a/coupon-core/src/main/java/com/bizguard/coupon/api/NewDto.java b/coupon-core/src/main/java/com/bizguard/coupon/api/NewDto.java
new file mode 100644
--- /dev/null
+++ b/coupon-core/src/main/java/com/bizguard/coupon/api/NewDto.java
@@ -0,0 +1,2 @@
+public class NewDto {
+}
"""

DELETE_FILE = """\
diff --git a/coupon-core/src/main/java/com/bizguard/coupon/api/OldDto.java b/coupon-core/src/main/java/com/bizguard/coupon/api/OldDto.java
deleted file mode 100644
--- a/coupon-core/src/main/java/com/bizguard/coupon/api/OldDto.java
+++ /dev/null
@@ -1,2 +0,0 @@
-public class OldDto {
-}
"""

RENAME_FILE = """\
diff --git a/coupon-core/src/main/java/com/bizguard/coupon/api/OldName.java b/coupon-core/src/main/java/com/bizguard/coupon/api/NewName.java
similarity index 100%
rename from coupon-core/src/main/java/com/bizguard/coupon/api/OldName.java
rename to coupon-core/src/main/java/com/bizguard/coupon/api/NewName.java
--- a/coupon-core/src/main/java/com/bizguard/coupon/api/OldName.java
+++ b/coupon-core/src/main/java/com/bizguard/coupon/api/NewName.java
@@ -1,2 +1,2 @@
 public class OldName {
 }
"""

PURE_RENAME = """\
diff --git a/docs/old.md b/docs/new.md
similarity index 100%
rename from docs/old.md
rename to docs/new.md
"""


def test_two_java_files_are_both_parsed() -> None:
    parsed = parse_unified(TWO_JAVA_FILES)
    assert len(parsed.files) == 2
    assert [file.new_path for file in parsed.files] == [
        "coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java",
        "merchant-service/src/main/java/com/bizguard/merchant/api/MerchantController.java",
    ]
    assert parsed.files[0].operation == "modify"
    assert parsed.files[1].operation == "modify"


def test_dto_field_removal_is_captured_alongside_decoy() -> None:
    parsed = parse_unified(DTO_AND_DECOY)
    assert len(parsed.files) == 2
    dto, decoy = parsed.files
    assert dto.removed_lines == ["    private String removedField;"]
    assert dto.added_lines == []
    assert decoy.added_lines == ["    // decoy changed"]
    assert decoy.removed_lines == ["    // decoy"]


def test_add_file_uses_null_old_path() -> None:
    parsed = parse_unified(ADD_FILE)
    assert len(parsed.files) == 1
    file = parsed.files[0]
    assert file.operation == "add"
    assert file.old_path is None
    assert file.new_path == "coupon-core/src/main/java/com/bizguard/coupon/api/NewDto.java"
    assert file.added_lines == ["public class NewDto {", "}"]


def test_delete_file_uses_null_new_path() -> None:
    parsed = parse_unified(DELETE_FILE)
    file = parsed.files[0]
    assert file.operation == "delete"
    assert file.old_path == "coupon-core/src/main/java/com/bizguard/coupon/api/OldDto.java"
    assert file.new_path is None
    assert file.removed_lines == ["public class OldDto {", "}"]


def test_rename_file_uses_rename_to_path() -> None:
    parsed = parse_unified(RENAME_FILE)
    file = parsed.files[0]
    assert file.operation == "rename"
    assert file.old_path == "coupon-core/src/main/java/com/bizguard/coupon/api/OldName.java"
    assert file.new_path == "coupon-core/src/main/java/com/bizguard/coupon/api/NewName.java"


def test_pure_rename_without_content_hunk_is_supported() -> None:
    file = parse_unified(PURE_RENAME).files[0]
    assert file.operation == "rename"
    assert file.old_path == "docs/old.md"
    assert file.new_path == "docs/new.md"
    assert file.hunks == []


def test_malformed_hunk_raises_fault() -> None:
    malformed = """\
diff --git a/x.java b/x.java
--- a/x.java
+++ b/x.java
@@ this is not a hunk header @@
 context
"""
    with pytest.raises(DiffParseError):
        parse_unified(malformed)


def test_binary_diff_raises_fault() -> None:
    binary = """\
diff --git a/x.bin b/x.bin
GIT binary patch
literal 0
"""
    with pytest.raises(DiffParseError):
        parse_unified(binary)


def test_parent_traversal_path_is_rejected() -> None:
    traversal = """\
diff --git a/../etc/passwd b/../etc/passwd
--- a/../etc/passwd
+++ b/../etc/passwd
@@ -1,1 +1,1 @@
 root:x:0:0
"""
    with pytest.raises(DiffParseError):
        parse_unified(traversal)


def test_truncated_hunk_raises_fault() -> None:
    truncated = """\
diff --git a/x.java b/x.java
--- a/x.java
+++ b/x.java
@@ -1,3 +1,3 @@
 context
"""
    with pytest.raises(DiffParseError):
        parse_unified(truncated)


def test_file_order_does_not_change_parsed_content() -> None:
    first = TWO_JAVA_FILES
    second = "\n".join(reversed(TWO_JAVA_FILES.split("\n\n")))
    assert sorted(parse_unified(first).files, key=lambda f: f.new_path or "") == sorted(
        parse_unified(second).files, key=lambda f: f.new_path or ""
    )


def test_empty_input_raises_fault() -> None:
    with pytest.raises(DiffParseError):
        parse_unified("")


def test_policy_validation_uses_unchanged_base_file_context(tmp_path: Path) -> None:
    migration = tmp_path / "db/V2__ledger.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text(
        "BEGIN TRANSACTION;\nUPDATE ledger SET status='SUCCESS';\nCOMMIT;\n",
        encoding="utf-8",
    )
    diff = """\
diff --git a/db/V2__ledger.sql b/db/V2__ledger.sql
--- a/db/V2__ledger.sql
+++ b/db/V2__ledger.sql
@@ -2,1 +2,1 @@
-UPDATE ledger SET status='SUCCESS';
+UPDATE ledger SET status='FAILED';
"""
    decision = ChangeEvaluator(tmp_path).evaluate(
        EvaluationRequest(diff_text=diff, repository_root=tmp_path, tests_passed=True)
    )
    assert decision.findings[0].violated is False
