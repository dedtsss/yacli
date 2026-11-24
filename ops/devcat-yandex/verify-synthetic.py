#!/usr/bin/env python3
"""Review-gated synthetic Mail+Disk acceptance against an isolated DevCat process."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import uuid

ALIASES = ("dedts", "ykkareliadom", "dvabobra2014")
ACCOUNT = "dedts"


class CandidateError(RuntimeError):
    def __init__(self, operation, code, kind="provider_or_tool"):
        self.operation = operation
        self.code = code
        self.kind = kind
        super().__init__(f"{operation}:{code}")


def service_environment():
    raw = subprocess.check_output(
        ["systemctl", "show", "yacli-devcat.service", "-p", "Environment", "--value"],
        text=True,
    )
    values = {}
    for word in shlex.split(raw):
        key, separator, value = word.partition("=")
        if separator and (key.startswith("YACLI_DEVCAT_") or key in {"YACLI_CONFIG_DIR", "YACLI_SECRET_BACKEND"}):
            values[key] = value
    return values


class Mcp:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.session = None
        self.request_id = 0

    def rpc(self, method, params):
        self.request_id += 1
        headers = {"Content-Type": "application/json"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(
            self.endpoint + "/mcp",
            json.dumps({"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}).encode(),
            headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=75) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                return json.load(response)
        except urllib.error.URLError as error:
            reason = getattr(error, "reason", None)
            kind = "transport_io" if reason else "http_transport"
            raise CandidateError(method, kind, kind) from None

    def call(self, name, arguments):
        response = self.rpc("tools/call", {"name": "yacli." + name, "arguments": arguments})
        if "error" in response:
            error = response["error"]
            code = error.get("data", {}).get("code", error.get("code", "RPC_ERROR"))
            message = str(error.get("message", ""))
            kind = "transport_io" if str(code) == "IO_ERROR" or any(token in message.lower() for token in (
                "broken pipe", "connection reset", "unexpected eof", "timed out", "closed connection"
            )) else "provider_or_tool"
            raise CandidateError(name, str(code), kind)
        result = response.get("result", {})
        value = result.get("structuredContent")
        if value is None:
            try:
                value = json.loads(result.get("content", [{}])[0].get("text", "{}"))
            except (ValueError, IndexError, AttributeError):
                raise CandidateError(name, "INVALID_RESPONSE") from None
        if result.get("isError") or value.get("ok") is False:
            raise CandidateError(name, str(value.get("code", "TOOL_ERROR")))
        return value

    def reviewed(self, name, arguments):
        preview = self.call(name, dict(arguments, dry_run=True))
        digest = preview.get("review_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise CandidateError(name, "NO_REVIEW_DIGEST")
        return self.call(name, dict(arguments, dry_run=False, review_sha256=digest))


def set_item(report, output, key, value):
    report[key] = value
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def add_case(report, output, operation, status="PASS", **details):
    report["cases"].append({"operation": operation, "status": status, **details})
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["cases"][-1], sort_keys=True), flush=True)


def folder_names(mcp):
    result = mcp.call("mail.folders", {"account": ACCOUNT})
    return {item.get("name") for item in result.get("items", []) if isinstance(item, dict)}


def listed_item(mcp, folder, subject):
    page = mcp.call("mail.list", {"account": ACCOUNT, "folder": folder, "limit": 100, "offset": 0})
    found = next((item for item in page.get("items", []) if item.get("subject") == subject), None)
    return page, found


def find_path(value, wanted_name):
    if isinstance(value, dict):
        if value.get("name") == wanted_name and isinstance(value.get("path"), str):
            return value["path"]
        for child in value.values():
            found = find_path(child, wanted_name)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_path(child, wanted_name)
            if found:
                return found
    return None


def configure_rule(mcp, operation, rule_id, **fields):
    args = {"account": ACCOUNT, "operation": operation, "id": rule_id, **fields}
    return mcp.reviewed("mail.rules.configure", args)


def apply_rule(mcp, rule_id, plan):
    args = {"account": ACCOUNT, "rule": rule_id, "plan_sha256": plan}
    preview = mcp.call("mail.rules.run", dict(args, dry_run=True))
    outer = preview.get("review_sha256")
    nested = preview.get("results", [{}])[0].get("preview", {}).get("review_sha256")
    if nested != plan or not isinstance(outer, str):
        raise CandidateError("mail.rules.run", "STALE_PREVIEW")
    result = mcp.call("mail.rules.run", dict(args, dry_run=False, review_sha256=outer))
    row = result.get("results", [{}])[0]
    if row.get("status") != "applied":
        raise CandidateError("mail.rules.run", "NOT_APPLIED")
    return row["run_id"]


def undo_rule(mcp, run_id):
    args = {"account": ACCOUNT, "run_id": run_id}
    first = mcp.call("mail.rules.undo", dict(args, dry_run=True))
    plan = first.get("plan_sha256")
    if not isinstance(plan, str):
        raise CandidateError("mail.rules.undo", "NO_PLAN_DIGEST")
    second = mcp.call("mail.rules.undo", dict(args, plan_sha256=plan, dry_run=True))
    outer = second.get("review_sha256")
    if second.get("plan_sha256") != plan or not isinstance(outer, str):
        raise CandidateError("mail.rules.undo", "STALE_PREVIEW")
    result = mcp.call("mail.rules.undo", dict(args, plan_sha256=plan, dry_run=False, review_sha256=outer))
    if result.get("status") != "undone":
        raise CandidateError("mail.rules.undo", "NOT_UNDONE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    binary = Path(args.binary).resolve(strict=True)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    marker = "YACLI-723-CANARY-" + uuid.uuid4().hex[:12].upper()
    suffix = uuid.uuid4().hex[:10].upper()
    folder_a, folder_b = f"YACLI-723-{suffix}-A", f"YACLI-723-{suffix}-B"
    rule_id = "canary_" + suffix.lower()
    disk_root = f"disk:/YACLI-723-{suffix}"
    report = {
        "state": "RUNNING", "scope": "isolated_synthetic_candidate", "account": ACCOUNT,
        "emails_sent": False, "external_recipients": False, "existing_mail_mutated": False,
        "existing_disk_files_mutated": False, "mail_watch_accessed": False,
        "synthetic_ids": {"marker": marker, "folders": [folder_a, folder_b], "rule_id": rule_id,
                           "disk_root": disk_root, "mail_subjects": [marker + " draft", marker + " updated"]},
        "cases": [], "cleanup_errors": [],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    process = None
    mcp = None
    temp_config_obj = None
    upload_path = None
    download_path = None
    created_folders = []
    test_folders_claimed = False
    disk_root_claimed = False
    updated_draft = None
    rule_claimed = False
    try:
        env = {key: value for key, value in os.environ.items() if key in {
            "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        }}
        env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        env.update(service_environment())
        source_config = Path(env["YACLI_CONFIG_DIR"]) / "accounts.toml"
        config = tomllib.loads(source_config.read_text())
        own_email = config["accounts"][ACCOUNT]["email"]
        upload_root = Path(env["YACLI_DEVCAT_UPLOAD_ROOTS"].split(":")[0])
        download_root = Path(env["YACLI_DEVCAT_ATTACHMENT_ROOT"])
        if not upload_root.is_dir() or not os.access(upload_root, os.W_OK):
            raise CandidateError("local synthetic upload", "UPLOAD_ROOT_UNAVAILABLE")
        if not download_root.is_dir() or not os.access(download_root, os.W_OK):
            raise CandidateError("local synthetic download", "DOWNLOAD_ROOT_UNAVAILABLE")
        service_user = subprocess.check_output(
            ["systemctl", "show", "yacli-devcat.service", "-p", "User", "--value"], text=True
        ).strip()
        import getpass
        if service_user != getpass.getuser():
            raise CandidateError("candidate process", "CANDIDATE_USER_MUST_MATCH_SERVICE_USER")
        temp_config_obj = tempfile.TemporaryDirectory(prefix="yacli-723-candidate-")
        temp_config = temp_config_obj.name
        os.chmod(temp_config, 0o700)
        shutil.copyfile(source_config, Path(temp_config) / "accounts.toml")
        os.chmod(Path(temp_config) / "accounts.toml", 0o600)
        env.update({
            "YACLI_CONFIG_DIR": temp_config, "YACLI_DEVCAT_MODE": "1",
            "YACLI_DEVCAT_ACCOUNT": ACCOUNT, "YACLI_DEVCAT_ALLOWED_ACCOUNTS": ",".join(ALIASES),
            "YACLI_DEVCAT_CAPABILITIES": "mail.read,mail.mutate,mail.delete,disk.read,disk.write,disk.delete",
                "YACLI_DEVCAT_DOWNLOAD_ROOTS": env.get("YACLI_DEVCAT_DOWNLOAD_ROOTS", env.get("YACLI_DEVCAT_ATTACHMENT_ROOT", "")),
            "YACLI_DEVCAT_TRUST_SECURE_TUNNEL": "1",
            "CREDENTIALS_DIRECTORY": "/run/credentials/yacli-devcat.service",
        })
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}"
        process = subprocess.Popen(
            [str(binary), "mcp", "--transport", "http", "--listen", f"127.0.0.1:{port}"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                with urllib.request.urlopen(endpoint + "/healthz", timeout=0.3) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.05)
        else:
            raise CandidateError("candidate health", "HEALTH_FAILED")
        mcp = Mcp(endpoint)
        mcp.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                "clientInfo": {"name": "yacli-723-synthetic", "version": "1"}})
        tools = mcp.rpc("tools/list", {}).get("result", {}).get("tools", [])
        names = {tool.get("name") for tool in tools}
        required = {
            "yacli.mail.folder.create", "yacli.mail.folder.delete", "yacli.mail.copy",
            "yacli.mail.move", "yacli.mail.special_folder", "yacli.mail.draft.create",
            "yacli.mail.draft.update", "yacli.mail.draft.trash", "yacli.mail.rules.configure",
            "yacli.mail.rules.preview", "yacli.mail.rules.run", "yacli.mail.rules.undo",
            "yacli.disk.info", "yacli.disk.mkdir", "yacli.disk.upload", "yacli.disk.read",
            "yacli.disk.permissions", "yacli.disk.copy", "yacli.disk.move",
            "yacli.disk.trash.list", "yacli.disk.trash.move", "yacli.disk.trash.restore",
            "yacli.disk.download",
        }
        risky_missing = [tool.get("name") for tool in tools if tool.get("name") in {
            "yacli.mail.folder.create", "yacli.mail.folder.delete", "yacli.mail.copy",
            "yacli.mail.move", "yacli.mail.special_folder", "yacli.mail.draft.create",
            "yacli.mail.draft.update", "yacli.mail.draft.trash", "yacli.mail.rules.configure",
            "yacli.mail.rules.run", "yacli.mail.rules.undo", "yacli.disk.mkdir",
            "yacli.disk.upload", "yacli.disk.copy", "yacli.disk.move",
            "yacli.disk.trash.move", "yacli.disk.trash.restore", "yacli.disk.download",
        } and not {"dry_run", "review_sha256"}.issubset(tool.get("inputSchema", {}).get("properties", {}))]
        if not required.issubset(names) or risky_missing:
            raise CandidateError("candidate catalog", "REQUIRED_TOOLS_OR_REVIEW_FIELDS_MISSING")
        add_case(report, output, "isolated_mutation_catalog", count=len(tools), review_fields="PASS")
        mcp.call("disk.info", {"account": ACCOUNT})
        add_case(report, output, "disk.info")

        existing = folder_names(mcp)
        if folder_a in existing or folder_b in existing:
            raise CandidateError("synthetic folder preflight", "NAME_COLLISION")
        test_folders_claimed = True
        # ID-ы сохранены в report до первой внешней мутации.
        mcp.reviewed("mail.folder.create", {"account": ACCOUNT, "folder": folder_a})
        created_folders.append(folder_a)
        add_case(report, output, "mail.folder.create.A")
        mcp.reviewed("mail.folder.create", {"account": ACCOUNT, "folder": folder_b})
        created_folders.append(folder_b)
        add_case(report, output, "mail.folder.create.B")

        configure_rule(mcp, "add", rule_id, mailbox=folder_a, subject_contains=marker, action="star")
        rule_claimed = True
        configure_rule(mcp, "enable", rule_id)
        configure_rule(mcp, "baseline", rule_id)
        add_case(report, output, "mail.rules.configure+baseline")

        mcp.reviewed("mail.draft.create", {
            "account": ACCOUNT, "to": own_email, "subject": marker + " draft",
            "text": "Synthetic acceptance object. Never send.",
        })
        drafts = mcp.call("mail.draft.list", {"account": ACCOUNT, "limit": 100, "offset": 0})
        draft_folder = drafts["folder"]
        draft = next((item for item in drafts.get("items", []) if item.get("subject") == marker + " draft"), None)
        if not draft or not draft.get("uid_validity"):
            raise CandidateError("mail.draft.create", "DRAFT_NOT_LISTED")
        updated_draft = {"folder": draft_folder, "uid": draft["uid"], "uid_validity": draft["uid_validity"]}
        mcp.reviewed("mail.draft.update", {
            "account": ACCOUNT, "uid": draft["uid"], "uid_validity": draft["uid_validity"],
            "to": own_email, "subject": marker + " updated", "text": "Synthetic updated draft. Never send.",
        })
        drafts = mcp.call("mail.draft.list", {"account": ACCOUNT, "limit": 100, "offset": 0})
        current = next((item for item in drafts.get("items", []) if item.get("subject") == marker + " updated"), None)
        if not current or not current.get("uid_validity"):
            raise CandidateError("mail.draft.update", "REPLACEMENT_NOT_LISTED")
        updated_draft.update(uid=current["uid"], uid_validity=current["uid_validity"])
        add_case(report, output, "mail.draft.create+update")

        mcp.reviewed("mail.copy", {
            "account": ACCOUNT, "source_folder": draft_folder, "uid": current["uid"],
            "uid_validity": current["uid_validity"], "destination_folder": folder_a,
        })
        page_a, copied = listed_item(mcp, folder_a, marker + " updated")
        if not copied or not page_a.get("uid_validity"):
            raise CandidateError("mail.copy", "SYNTHETIC_COPY_NOT_LISTED")
        message = {"folder": folder_a, "uid": copied["uid"], "uid_validity": page_a["uid_validity"]}
        add_case(report, output, "mail.copy")

        preview = mcp.call("mail.rules.preview", {"account": ACCOUNT, "rule": rule_id})
        plan = preview.get("results", [{}])[0].get("preview", {}).get("review_sha256")
        if not isinstance(plan, str):
            raise CandidateError("mail.rules.preview", "NO_PLAN_DIGEST")
        run_id = apply_rule(mcp, rule_id, plan)
        add_case(report, output, "mail.rules.run", run_id=run_id)
        undo_rule(mcp, run_id)
        add_case(report, output, "mail.rules.undo")

        mcp.reviewed("mail.move", {
            "account": ACCOUNT, "source_folder": folder_a, "uid": message["uid"],
            "uid_validity": message["uid_validity"], "destination_folder": folder_b,
        })
        page_b, moved = listed_item(mcp, folder_b, marker + " updated")
        if not moved or not page_b.get("uid_validity"):
            raise CandidateError("mail.move", "MOVED_COPY_NOT_LISTED")
        message.update(folder=folder_b, uid=moved["uid"], uid_validity=page_b["uid_validity"])
        add_case(report, output, "mail.move")

        folders = mcp.call("mail.folders", {"account": ACCOUNT}).get("items", [])
        junk_available = any(any(str(flag).lower() in {"\\junk", "\\spam"} for flag in f.get("attributes", [])) for f in folders)
        if junk_available:
            mcp.reviewed("mail.special_folder", {
                "account": ACCOUNT, "uid": message["uid"], "uid_validity": message["uid_validity"],
                "source_folder": folder_b, "action": "move_to_spam",
            })
            spam_list = mcp.call("mail.folders", {"account": ACCOUNT}).get("items", [])
            spam_folder = next((f.get("name") for f in spam_list if any(str(flag).lower() in {"\\junk", "\\spam"} for flag in f.get("attributes", []))), None)
            if not spam_folder:
                raise CandidateError("mail.special_folder", "SPAM_FOLDER_NOT_LISTED")
            spam_page, spam_item = listed_item(mcp, spam_folder, marker + " updated")
            if not spam_item:
                raise CandidateError("mail.special_folder", "SPAM_COPY_NOT_LISTED")
            mcp.reviewed("mail.special_folder", {
                "account": ACCOUNT, "uid": spam_item["uid"], "uid_validity": spam_page["uid_validity"],
                "destination_folder": folder_b, "action": "restore_from_spam",
            })
            page_b, moved = listed_item(mcp, folder_b, marker + " updated")
            message.update(folder=folder_b, uid=moved["uid"], uid_validity=page_b["uid_validity"])
            add_case(report, output, "mail.spam+restore")
        else:
            add_case(report, output, "mail.spam+restore", "UNSUPPORTED", reason="provider_did_not_advertise_junk")

        mcp.reviewed("mail.special_folder", {
            "account": ACCOUNT, "uid": message["uid"], "uid_validity": message["uid_validity"],
            "source_folder": folder_b, "action": "move_to_trash",
        })
        trash_items = mcp.call("mail.folders", {"account": ACCOUNT}).get("items", [])
        trash_folder = next((f.get("name") for f in trash_items if any(str(flag).lower() == "\\trash" for flag in f.get("attributes", []))), None)
        if not trash_folder:
            raise CandidateError("mail.special_folder", "TRASH_FOLDER_NOT_LISTED")
        trash_page, trash_item = listed_item(mcp, trash_folder, marker + " updated")
        if not trash_item:
            raise CandidateError("mail.special_folder", "TRASH_COPY_NOT_LISTED")
        mcp.reviewed("mail.special_folder", {
            "account": ACCOUNT, "uid": trash_item["uid"], "uid_validity": trash_page["uid_validity"],
            "destination_folder": folder_b, "action": "restore_from_trash",
        })
        page_b, moved = listed_item(mcp, folder_b, marker + " updated")
        message.update(folder=folder_b, uid=moved["uid"], uid_validity=page_b["uid_validity"])
        add_case(report, output, "mail.trash+restore")

        mcp.reviewed("mail.special_folder", {
            "account": ACCOUNT, "uid": message["uid"], "uid_validity": message["uid_validity"],
            "source_folder": folder_b, "action": "move_to_trash",
        })
        mcp.reviewed("mail.draft.trash", {
            "account": ACCOUNT, "uid": updated_draft["uid"], "uid_validity": updated_draft["uid_validity"],
        })
        add_case(report, output, "mail.draft.trash+synthetic_copy_trash")

        # Disk canary uses a unique namespace and a 2 MiB streaming upload.
        root_listing = mcp.call("disk.list", {"account": ACCOUNT, "path": "disk:/", "limit": 1000, "offset": 0})
        if find_path(root_listing, disk_root.rsplit("/", 1)[-1]):
            raise CandidateError("disk synthetic preflight", "NAME_COLLISION")
        disk_root_claimed = True
        mcp.reviewed("disk.mkdir", {"account": ACCOUNT, "path": disk_root})
        add_case(report, output, "disk.mkdir")
        data = bytes((index * 31 + 7) % 256 for index in range(2 * 1024 * 1024))
        expected_sha = hashlib.sha256(data).hexdigest()
        fd, filename = tempfile.mkstemp(prefix=marker + "-", suffix=".bin", dir=upload_root)
        upload_path = Path(filename)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        small_name, large_name = "small.bin", "large.bin"
        small_data = b"YACLI-723 synthetic streaming check\n"
        upload_path.write_bytes(small_data)
        small_path = f"{disk_root}/{small_name}"
        mcp.reviewed("disk.upload", {"account": ACCOUNT, "source": str(upload_path), "path": small_path})
        add_case(report, output, "disk.upload.small")
        mcp.reviewed("disk.copy", {"account": ACCOUNT, "from_path": small_path, "to_path": f"{disk_root}/copy.bin"})
        mcp.reviewed("disk.move", {"account": ACCOUNT, "from_path": f"{disk_root}/copy.bin", "to_path": f"{disk_root}/moved.bin"})
        read = mcp.call("disk.read", {"account": ACCOUNT, "path": f"{disk_root}/moved.bin"})
        if base64.b64decode(read.get("data", "")) != small_data:
            raise CandidateError("disk.read", "CHECKSUM_MISMATCH")
        permission = mcp.call("disk.permissions", {"account": ACCOUNT, "path": f"{disk_root}/moved.bin"})
        if permission.get("public_access", {}).get("enabled") is not False:
            raise CandidateError("disk.permissions", "SYNTHETIC_FILE_NOT_PRIVATE")
        add_case(report, output, "disk.copy+move+read+permissions", bytes=len(small_data))

        upload_path.write_bytes(data)
        large_path = f"{disk_root}/{large_name}"
        mcp.reviewed("disk.upload", {"account": ACCOUNT, "source": str(upload_path), "path": large_path})
        fd, filename = tempfile.mkstemp(prefix=marker + "-download-", suffix=".bin", dir=download_root)
        os.close(fd)
        Path(filename).unlink()
        download_path = Path(filename)
        mcp.reviewed("disk.download", {
            "account": ACCOUNT, "path": large_path, "output_path": str(download_path), "force": False,
        })
        if not download_path.is_file() or hashlib.sha256(download_path.read_bytes()).hexdigest() != expected_sha:
            raise CandidateError("disk.download", "LARGE_FILE_CHECKSUM_MISMATCH")
        add_case(report, output, "disk.upload+download.large", bytes=len(data), sha256=expected_sha)

        mcp.reviewed("disk.trash.move", {"account": ACCOUNT, "path": small_path})
        trash = mcp.call("disk.trash.list", {"account": ACCOUNT, "path": "trash:/", "limit": 1000, "offset": 0})
        trash_path = find_path(trash, small_name)
        if not trash_path:
            raise CandidateError("disk.trash.list", "SYNTHETIC_FILE_NOT_LISTED")
        mcp.reviewed("disk.trash.restore", {"account": ACCOUNT, "trash_path": trash_path})
        restored = mcp.call("disk.read", {"account": ACCOUNT, "path": small_path})
        if base64.b64decode(restored.get("data", "")) != small_data:
            raise CandidateError("disk.trash.restore", "RESTORE_CHECKSUM_MISMATCH")
        add_case(report, output, "disk.trash.move+list+restore+read")

        report["state"] = "PASS"
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except CandidateError as error:
        report["state"] = "BLOCKED"
        report["failure"] = {"operation": error.operation, "code": error.code, "kind": error.kind}
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"state": report["state"], **report["failure"]}, sort_keys=True), flush=True)
    except Exception as error:
        report["state"] = "BLOCKED"
        report["failure"] = {"operation": "runner", "code": type(error).__name__, "kind": "runner"}
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"state": report["state"], **report["failure"]}, sort_keys=True), flush=True)
    finally:
        if upload_path:
            upload_path.unlink(missing_ok=True)
        if download_path:
            download_path.unlink(missing_ok=True)
        if mcp:
            # Cleanup is restricted to exact random identifiers persisted before the first mutation.
            try:
                drafts = mcp.call("mail.draft.list", {"account": ACCOUNT, "limit": 100, "offset": 0})
                for item in drafts.get("items", []):
                    if str(item.get("subject", "")).startswith(report["synthetic_ids"]["marker"]) and item.get("uid_validity"):
                        mcp.reviewed("mail.draft.trash", {
                            "account": ACCOUNT, "uid": item["uid"], "uid_validity": item["uid_validity"],
                        })
            except Exception:
                report["cleanup_errors"].append("draft_cleanup_unverified")
            try:
                existing = folder_names(mcp) if test_folders_claimed else set(created_folders)
                candidates = created_folders + [
                    name for name in report["synthetic_ids"]["folders"]
                    if test_folders_claimed and name in existing and name not in created_folders
                ]
                for folder in candidates:
                    if folder not in existing:
                        continue
                    page = mcp.call("mail.list", {"account": ACCOUNT, "folder": folder, "limit": 100, "offset": 0})
                    items = page.get("items", [])
                    if any(not str(item.get("subject", "")).startswith(report["synthetic_ids"]["marker"]) for item in items):
                        report["cleanup_errors"].append("folder_contains_non_synthetic_mail")
                        continue
                    for item in items:
                        if item.get("uid_validity"):
                            mcp.reviewed("mail.special_folder", {
                                "account": ACCOUNT, "uid": item["uid"], "uid_validity": item["uid_validity"],
                                "source_folder": folder, "action": "move_to_trash",
                            })
                            page = mcp.call("mail.list", {"account": ACCOUNT, "folder": folder, "limit": 100, "offset": 0})
                            items = page.get("items", [])
                    if not items:
                        mcp.reviewed("mail.folder.delete", {"account": ACCOUNT, "folder": folder})
                add_case(report, output, "cleanup.mail_synthetic_folders")
            except Exception:
                report["cleanup_errors"].append("folder_cleanup_unverified")
            try:
                if rule_claimed:
                    rules = mcp.call("mail.rules.list", {"account": ACCOUNT}).get("items", [])
                    if any(item.get("rule", {}).get("id") == rule_id and item.get("rule", {}).get("account") == ACCOUNT for item in rules):
                        configure_rule(mcp, "disable", rule_id)
                        configure_rule(mcp, "delete", rule_id)
                add_case(report, output, "cleanup.local_rule")
            except Exception:
                report["cleanup_errors"].append("temporary_rule_cleanup_failed")
            try:
                if disk_root_claimed:
                    current_root = mcp.call("disk.list", {"account": ACCOUNT, "path": "disk:/", "limit": 1000, "offset": 0})
                    if find_path(current_root, disk_root.rsplit("/", 1)[-1]):
                        mcp.reviewed("disk.trash.move", {"account": ACCOUNT, "path": disk_root})
                add_case(report, output, "cleanup.disk_to_reversible_trash")
            except Exception:
                report["cleanup_errors"].append("disk_cleanup_unverified")
        if process:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            report["candidate_process_stopped"] = True
        if temp_config_obj:
            temp_config_obj.cleanup()
        if report["cleanup_errors"] and report["state"] == "PASS":
            report["state"] = "BLOCKED"
            report["failure"] = {"operation": "cleanup", "code": "CLEANUP_UNVERIFIED", "kind": "cleanup"}
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if report["state"] == "PASS" and not report["cleanup_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
