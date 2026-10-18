from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any


PROFILE_DIR = Path(__file__).resolve().parents[1] / "config" / "remnawave"
BRIDGE_PATH = PROFILE_DIR / "bridge-profile.json"
EXIT_PATH = PROFILE_DIR / "exit-profile.json"
PRODUCTION_EXIT_PATH = (
    Path(__file__).resolve().parents[1]
    / "deploy"
    / "production"
    / "remnawave"
    / "germany-exit-profile.json"
)
RESILIENT_EXIT_PATH = PRODUCTION_EXIT_PATH.with_name(
    "germany-exit-profile-resilient.json"
)
REQUIRED_PLACEHOLDER = "REPLACE_WITH_"


def _load_profile(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as profile_file:
        profile = json.load(profile_file)
    if not isinstance(profile, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return profile


def _all_strings(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _all_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_strings(child)
    elif isinstance(value, str):
        yield value


class RemnawaveProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = _load_profile(BRIDGE_PATH)
        cls.exit = _load_profile(EXIT_PATH)
        cls.production_exit = _load_profile(PRODUCTION_EXIT_PATH)
        cls.resilient_exit = _load_profile(RESILIENT_EXIT_PATH)

    def test_bridge_fails_closed_through_exit_only(self) -> None:
        bridge = self.bridge
        self.assertEqual([item["tag"] for item in bridge["inbounds"]], ["JOPES_BRIDGE_IN"])
        bridge_inbound = bridge["inbounds"][0]
        self.assertEqual(bridge_inbound["protocol"], "vless")
        self.assertEqual(bridge_inbound["streamSettings"]["security"], "reality")
        self.assertEqual(bridge_inbound["streamSettings"]["network"], "raw")
        self.assertEqual(
            [(item["tag"], item["protocol"]) for item in bridge["outbounds"]],
            [("BLOCK", "blackhole"), ("TO_EXIT", "vless")],
        )
        self.assertEqual(bridge["outbounds"][0]["tag"], "BLOCK")
        self.assertEqual(
            bridge["outbounds"][1]["settings"]["vnext"][0]["users"][0]["encryption"],
            "none",
        )
        self.assertEqual(
            bridge["inbounds"][0]["settings"]["clients"],
            [],
            "Remnawave must populate subscriber clients from managed users",
        )
        self.assertEqual(
            bridge["routing"]["rules"],
            [
                {
                    "type": "field",
                    "inboundTag": ["JOPES_BRIDGE_IN"],
                    "outboundTag": "TO_EXIT",
                }
            ],
        )
        self.assertFalse(
            any(item["protocol"] == "freedom" for item in bridge["outbounds"]),
            "Bridge must never have a direct internet outbound",
        )
        outbound_tags = {item["tag"] for item in bridge["outbounds"]}
        for rule in bridge["routing"]["rules"]:
            self.assertIn(rule["outboundTag"], outbound_tags)

    def test_exit_allows_direct_egress_only_for_exit_inbound(self) -> None:
        exit_profile = self.exit
        self.assertEqual([item["tag"] for item in exit_profile["inbounds"]], ["JOPES_EXIT_IN"])
        exit_inbound = exit_profile["inbounds"][0]
        self.assertEqual(exit_inbound["protocol"], "vless")
        self.assertEqual(exit_inbound["streamSettings"]["security"], "reality")
        self.assertEqual(exit_inbound["streamSettings"]["network"], "raw")
        self.assertEqual(
            [(item["tag"], item["protocol"]) for item in exit_profile["outbounds"]],
            [("BLOCK", "blackhole"), ("DIRECT", "freedom")],
        )
        self.assertEqual(exit_profile["outbounds"][0]["tag"], "BLOCK")
        self.assertEqual(exit_profile["inbounds"][0]["settings"]["clients"], [])
        self.assertEqual(
            exit_profile["routing"]["rules"],
            [
                {
                    "type": "field",
                    "inboundTag": ["JOPES_EXIT_IN"],
                    "outboundTag": "DIRECT",
                }
            ],
        )
        outbound_tags = {item["tag"] for item in exit_profile["outbounds"]}
        for rule in exit_profile["routing"]["rules"]:
            self.assertIn(rule["outboundTag"], outbound_tags)

    def test_templates_have_no_real_values_or_unbound_placeholders(self) -> None:
        expected = {
            "REPLACE_WITH_BRIDGE_REALITY_TARGET:443",
            "REPLACE_WITH_BRIDGE_SERVER_NAME",
            "REPLACE_WITH_BRIDGE_REALITY_PRIVATE_KEY",
            "REPLACE_WITH_BRIDGE_SHORT_ID",
            "REPLACE_WITH_EXIT_ADDRESS",
            "REPLACE_WITH_EXIT_SERVICE_USER_UUID",
            "REPLACE_WITH_EXIT_SERVER_NAME",
            "REPLACE_WITH_EXIT_REALITY_PUBLIC_KEY",
            "REPLACE_WITH_EXIT_SHORT_ID",
            "REPLACE_WITH_EXIT_REALITY_TARGET:443",
            "REPLACE_WITH_EXIT_REALITY_PRIVATE_KEY",
        }
        strings = set(_all_strings(self.bridge)) | set(_all_strings(self.exit))
        placeholders = {value for value in strings if REQUIRED_PLACEHOLDER in value}
        self.assertEqual(placeholders, expected)
        sensitive_values = [
            self.bridge["inbounds"][0]["streamSettings"]["realitySettings"][
                "privateKey"
            ],
            self.bridge["outbounds"][1]["settings"]["vnext"][0]["users"][0]["id"],
            self.bridge["outbounds"][1]["streamSettings"]["realitySettings"][
                "password"
            ],
            self.exit["inbounds"][0]["streamSettings"]["realitySettings"][
                "privateKey"
            ],
        ]
        self.assertTrue(
            all(value.startswith(REQUIRED_PLACEHOLDER) for value in sensitive_values),
            "Credential fields must remain explicit placeholders",
        )

    def test_vless_flow_is_explicitly_disabled_on_both_nodes(self) -> None:
        bridge_in = self.bridge["inbounds"][0]
        exit_in = self.exit["inbounds"][0]
        exit_user = self.bridge["outbounds"][1]["settings"]["vnext"][0]["users"][0]
        self.assertEqual(bridge_in["settings"]["flow"], "")
        self.assertEqual(exit_in["settings"]["flow"], "")
        self.assertEqual(exit_user["flow"], "")

    def test_production_exit_profile_uses_a_distinct_public_tcp_port(self) -> None:
        profile = self.production_exit
        inbound = profile["inbounds"][0]
        self.assertEqual(inbound["tag"], "JOPES_EXIT_IN")
        self.assertEqual(inbound["port"], 2053)
        self.assertEqual(inbound["protocol"], "vless")
        self.assertEqual(inbound["streamSettings"]["network"], "raw")
        self.assertEqual(inbound["streamSettings"]["security"], "reality")
        self.assertEqual(inbound["settings"]["clients"], [])
        self.assertEqual(
            profile["routing"]["rules"],
            [
                {
                    "type": "field",
                    "inboundTag": ["JOPES_EXIT_IN"],
                    "outboundTag": "DIRECT",
                }
            ],
        )
        self.assertEqual(
            {
                value
                for value in _all_strings(profile)
                if value.startswith(REQUIRED_PLACEHOLDER)
            },
            {
                "REPLACE_WITH_GERMANY_REALITY_TARGET:443",
                "REPLACE_WITH_GERMANY_SERVER_NAME",
                "REPLACE_WITH_GERMANY_REALITY_PRIVATE_KEY",
                "REPLACE_WITH_GERMANY_SHORT_ID",
            },
        )


    def test_resilient_profile_preserves_raw_and_adds_xhttp_reality(self) -> None:
        profile = self.resilient_exit
        raw_inbound, xhttp_inbound = profile["inbounds"]
        production_inbound = self.production_exit["inbounds"][0]

        self.assertEqual(raw_inbound, production_inbound)
        self.assertEqual(xhttp_inbound["tag"], "JOPES_XHTTP_IN")
        self.assertEqual(xhttp_inbound["port"], 8443)
        self.assertEqual(xhttp_inbound["protocol"], "vless")
        self.assertEqual(xhttp_inbound["settings"]["clients"], [])
        self.assertEqual(xhttp_inbound["settings"]["flow"], "")
        self.assertEqual(xhttp_inbound["streamSettings"]["network"], "xhttp")
        self.assertEqual(xhttp_inbound["streamSettings"]["security"], "reality")
        self.assertEqual(
            xhttp_inbound["streamSettings"]["realitySettings"],
            production_inbound["streamSettings"]["realitySettings"],
            "Both inbounds must reuse the same validated REALITY identity",
        )
        self.assertEqual(
            xhttp_inbound["streamSettings"]["xhttpSettings"],
            {
                "path": "/jopes-xhttp",
                "mode": "auto",
                "scMaxEachPostBytes": "500000-1000000",
                "scMinPostsIntervalMs": "10-50",
                "scStreamUpServerSecs": "20-80",
            },
        )
        self.assertEqual(
            [(item["tag"], item["protocol"]) for item in profile["outbounds"]],
            [("BLOCK", "blackhole"), ("DIRECT", "freedom")],
        )
        self.assertEqual(
            profile["routing"]["rules"],
            [
                {
                    "type": "field",
                    "inboundTag": ["JOPES_EXIT_IN", "JOPES_XHTTP_IN"],
                    "outboundTag": "DIRECT",
                }
            ],
        )
        self.assertEqual(
            {
                value
                for value in _all_strings(profile)
                if value.startswith(REQUIRED_PLACEHOLDER)
            },
            {
                "REPLACE_WITH_GERMANY_REALITY_TARGET:443",
                "REPLACE_WITH_GERMANY_SERVER_NAME",
                "REPLACE_WITH_GERMANY_REALITY_PRIVATE_KEY",
                "REPLACE_WITH_GERMANY_SHORT_ID",
            },
        )


if __name__ == "__main__":
    unittest.main()
