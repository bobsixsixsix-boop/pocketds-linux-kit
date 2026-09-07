#!/usr/bin/env python3
"""Source-candidate binding; never promotes the deployed haptics binary."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components/inputplumber"
lock = json.loads((COMPONENT / "inputplumber-mouse-clear-source-lock.json").read_text())
assert lock["schema"] == "pocketds.inputplumber-mouse-clear-source.v1"
assert lock["upstream_commit"] == "d3932cb4e0de3eb47688e381b5eb5334ebac6254"
assert lock["binary"] is None and lock["install_ready"] is False
assert lock["hardware_validated"] is False
digest = lambda data: hashlib.sha256(data).hexdigest()
patch = (COMPONENT / lock["patch"]["path"]).read_bytes()
assert (len(patch), digest(patch)) == (lock["patch"]["size"], lock["patch"]["sha256"])
assert digest((COMPONENT / lock["base_patch"]["path"]).read_bytes()) == lock["base_patch"]["sha256"]
builder = (COMPONENT / lock["release_builder"]["repository_path"]).read_bytes()
assert digest(builder) == lock["release_builder"]["sha256"]
text = patch.decode()
files = [line.split(" b/", 1)[1] for line in text.splitlines() if line.startswith("diff --git ")]
assert files == [lock["mouse_source"]["path"], lock["release_builder"]["upstream_path"]]
new_file = text.split("+++ b/" + lock["release_builder"]["upstream_path"] + "\n", 1)[1]
copied = "".join(line[1:] for line in new_file.splitlines(keepends=True) if line.startswith("+"))
assert copied.encode() == builder
# This is the production use of the independently compiled Rust builder,
# not just an unused fixture added next to unchanged clear_state.
assert "+        let events = button_releases::release_events()" in text
assert "+            .map(|(code, value)| InputEvent::new(EventType::KEY.0, code, value));" in text
assert "+        if let Err(error) = self.device.emit(&events) {" in text
deployed = json.loads((COMPONENT / "inputplumber-haptics-source-lock.json").read_text())
assert deployed["downstream_patch_sha256"] == lock["base_patch"]["sha256"]
assert deployed["binary_sha256"] == "4dbb8a7dc494e27abdbe3b38191dabfaef54caa8f6f2c7357bc84008bbfb488b"
print("  [OK] mouse-clear source candidate, exact builder and public haptics build binding")
