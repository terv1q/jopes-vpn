# Remnawave Xray Config Profiles

These are profile templates, not deployable configs. Replace every `REPLACE_WITH_*` value with environment-specific data in Remnawave; never commit completed profiles or real credentials here.

## User and service-user model

Remnawave manages users on VLESS inbounds. Keep each inbound `settings.clients` array empty in the profile; Panel populates it for assigned users. Assign ordinary subscribers only to `JOPES_BRIDGE_IN`.

The bridge-to-exit credential is a separate Remnawave-managed service user:

1. On the exit Node, assign `exit-profile.json` and enable `JOPES_EXIT_IN`.
2. Create a dedicated service user in Remnawave and assign it access to `JOPES_EXIT_IN` through an Internal Squad.
3. Use that user's VLESS UUID as `REPLACE_WITH_EXIT_SERVICE_USER_UUID` in the bridge profile. The UUID is a credential; do not put a real value in source control or logs.
4. Set the bridge outbound's REALITY public key, server name, short ID, and exit address from the exit endpoint configuration.
5. On the bridge Node, assign `bridge-profile.json` and enable `JOPES_BRIDGE_IN`. Put ordinary subscribers in a Squad that exposes this inbound.

The service user remains managed by Remnawave but its UUID is referenced by the bridge's fixed outbound. It is distinct from subscriber UUIDs. The live lab verified one active service-user assignment and successful use by the bridge; rotation/revocation and coordinated replacement of the outbound remain untested.

## Routing safety

- Bridge has no `freedom` outbound. `BLOCK` is first so unmatched traffic fails closed; the only subscriber route sends `JOPES_BRIDGE_IN` to `TO_EXIT`.
- Exit also defaults to `BLOCK`. Only `JOPES_EXIT_IN` is explicitly routed to `DIRECT`, so direct internet egress occurs at the exit Node, not the bridge.
- Keep the bridge and exit profiles on their respective Nodes. Do not combine both profiles into one Node or expose the exit inbound to ordinary subscriber Squads.
- Remnawave Node may prepend its internal API routing rule when applying a profile. The local profile validator checks the user-authored rules before Panel/Node transformation; a live Node check is still required.

## Flow control

Both VLESS inbounds explicitly set `"flow": ""` to request standard VLESS without Vision. The bridge's VLESS outbound service-user entry also sets an empty flow. In the tested Panel 3.4.5 lab, the real bridge subscription omitted `flow`; this confirms that output for this profile/version, not all Panel versions or configurations.

## Local checks

From repository root:

```powershell
python -m unittest discover -s tests -p 'test_remnawave_profiles.py' -v
$env:XRAY_BINARY = 'C:\path\to\xray.exe'
python -m unittest discover -s tests -p 'test_bridge_exit_lab.py' -v
```

The integration test fills placeholders only in a temporary directory and runs the resulting configs against the pinned Xray version. It does not invoke Remnawave or Happ and does not prove whitelist bypass.
