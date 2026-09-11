"""Closed parsing tests for a locally supplied Lark Docx selector."""

import hashlib
import sys
import unittest
from dataclasses import FrozenInstanceError, MISSING, fields
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "knowledge-distiller" / "scripts"))

from knowledge_distiller import lark_selector  # noqa: E402


TOKEN = "doxcn1234567890AbCdEfGhIjKl"
EXPECTED_COMMITMENT = "sha256:" + hashlib.sha256(
    b"lark-docx-selector/v1\0" + TOKEN.encode("ascii")
).hexdigest()


class DocumentSelectorTest(unittest.TestCase):
    def assert_invalid(self, value):
        with self.assertRaises(lark_selector.SelectorError) as caught:
            lark_selector.parse_document_selector(value)
        error = caught.exception
        self.assertEqual(error.code, "invalid-selector")
        self.assertEqual(error.args, ("invalid-selector",))
        self.assertEqual(str(error), "invalid-selector")
        self.assertNotIn(str(value), repr(vars(error)))

    def test_token_and_exact_url_produce_the_same_private_selector(self):
        token = lark_selector.parse_document_selector(TOKEN)
        url = lark_selector.parse_document_selector(
            "https://tenant.larkoffice.com/docx/" + TOKEN
        )

        self.assertEqual(token, url)
        self.assertEqual(token.token, TOKEN)
        self.assertEqual(token.commitment, EXPECTED_COMMITMENT)
        self.assertEqual(lark_selector.selector_commitment(TOKEN), EXPECTED_COMMITMENT)
        self.assertNotIn(TOKEN, repr(token))
        self.assertEqual(
            tuple(item.name for item in fields(lark_selector.ParsedDocumentSelector)),
            ("token", "commitment"),
        )
        self.assertTrue(
            all(item.default is MISSING and item.default_factory is MISSING
                for item in fields(lark_selector.ParsedDocumentSelector))
        )
        with self.assertRaises(FrozenInstanceError):
            token.token = "other"

    def test_accepts_every_allowed_lowercase_tenant_suffix(self):
        for host in (
            "tenant.larkoffice.com",
            "tenant.larksuite.com",
            "tenant.feishu.cn",
            "tenant-with-hyphen.region-1.larkoffice.com",
        ):
            with self.subTest(host=host):
                self.assertEqual(
                    lark_selector.parse_document_selector("https://%s/docx/%s" % (host, TOKEN)).token,
                    TOKEN,
                )

    def test_accepts_consecutive_interior_label_hyphens(self):
        selector = lark_selector.parse_document_selector(
            "https://team--one.larkoffice.com/docx/" + TOKEN
        )

        self.assertEqual(selector.token, TOKEN)

    def test_enforces_dns_label_and_hostname_length_boundaries(self):
        label_63 = "a" + ("b" * 61) + "c"
        label_64 = label_63 + "d"
        host_253 = ".".join(
            ("a" * 63, "b" * 63, "c" * 63, "d" * 46, "larkoffice", "com")
        )
        self.assertEqual(len(host_253), 253)

        for host in (label_63 + ".larkoffice.com", host_253):
            with self.subTest(accepted_host_length=len(host)):
                self.assertEqual(
                    lark_selector.parse_document_selector(
                        "https://%s/docx/%s" % (host, TOKEN)
                    ).token,
                    TOKEN,
                )
        for host in (label_64 + ".larkoffice.com", host_253.replace("d" * 46, "d" * 47)):
            with self.subTest(rejected_host_length=len(host)):
                self.assert_invalid("https://%s/docx/%s" % (host, TOKEN))

    def test_rejects_noncanonical_tokens_and_non_string_scalars(self):
        class TextSubclass(str):
            __slots__ = ()

        for value in (
            TOKEN[:-1], TOKEN + "x", TOKEN[:-1] + "_", TOKEN[:-1] + "-",
            TOKEN[:-1] + "中", None, 3, True, TextSubclass(TOKEN),
        ):
            with self.subTest(value=repr(value)):
                self.assert_invalid(value)
        with self.assertRaises(lark_selector.SelectorError):
            lark_selector.selector_commitment(TextSubclass(TOKEN))

    def test_rejects_noncanonical_url_forms_without_normalizing_them(self):
        invalid = (
            "HTTPS://tenant.larkoffice.com/docx/" + TOKEN,
            "https://Tenant.larkoffice.com/docx/" + TOKEN,
            "https://larkoffice.com/docx/" + TOKEN,
            "https://.larkoffice.com/docx/" + TOKEN,
            "https://tenant-.larkoffice.com/docx/" + TOKEN,
            "https://-tenant.larkoffice.com/docx/" + TOKEN,
            "https://tenant..larkoffice.com/docx/" + TOKEN,
            "https://user@tenant.larkoffice.com/docx/" + TOKEN,
            "https://tenant.larkoffice.com:443/docx/" + TOKEN,
            "https://tenant.larkoffice.com:444/docx/" + TOKEN,
            "https://tenant.larkoffice.com/docx/%64" + TOKEN[1:],
            "https://tenant.larkoffice.com/docx/" + TOKEN + "?x=1",
            "https://tenant.larkoffice.com/docx/" + TOKEN + "#part",
            "https://tenant.larkoffice.com/docx/" + TOKEN + "/",
            "https://tenant.larkoffice.com/wiki/" + TOKEN,
            "https://tenant.larkoffice.com/docx/" + TOKEN + "/child",
            "https://tenant.larkoffice.com/Docx/" + TOKEN,
            "https://tenant.larkoffice.com/docx/" + TOKEN + "\u0080",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assert_invalid(value)


if __name__ == "__main__":
    unittest.main()
