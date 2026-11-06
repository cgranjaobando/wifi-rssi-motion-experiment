import argparse
import ipaddress
import json
import re
import subprocess
from dataclasses import asdict, dataclass


DEFAULT_SERVER_URL = "http://192.168.40.3:8877"
DEFAULT_GATEWAY_ADDRESS = "192.168.40.1"
DEFAULT_INTERVAL_SECONDS = 1.0
RECEIVER_ID_PREFIX = "receiver"
ARP_INTERFACE_PATTERN = re.compile(r"^Interface:\s+([0-9.]+)\s+---", re.IGNORECASE)
ARP_ENTRY_PATTERN = re.compile(
    r"^\s*([0-9.]+)\s+([0-9a-f-]{17})\s+(\w+)\s*$",
    re.IGNORECASE,
)
BROADCAST_MAC_ADDRESS = "ff-ff-ff-ff-ff-ff"
ZERO_MAC_ADDRESS = "00-00-00-00-00-00"
NETWORK_CONTROL_CLASSIFICATION = "network_control"
GATEWAY_CLASSIFICATION = "gateway_router"
WINDOWS_RECEIVER_CLASSIFICATION = "windows_receiver_candidate"
ANDROID_RECEIVER_CLASSIFICATION = "possible_android_or_appliance"
PRIVATE_MAC_CLASSIFICATION = "unverified_private_mac"
GENERIC_RECEIVER_CLASSIFICATION = "receiver_candidate"

VENDOR_HINTS_BY_PREFIX = {
    "58-72-c9": "ZTE",
    "b4-45-06": "Dell",
    "a0-d7-f3": "Samsung",
}


@dataclass(frozen=True)
class ArpEntry:
    interface_address: str
    ip_address: str
    mac_address: str
    entry_type: str


@dataclass(frozen=True)
class ReceiverEnrollmentCandidate:
    ip_address: str
    mac_address: str
    vendor_hint: str
    classification: str
    receiver_id: str
    receiver_label: str
    command: str


def run_command(arguments: list[str]) -> str:
    completed = subprocess.run(
        arguments,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return f"{completed.stdout}\n{completed.stderr}".strip()


def parse_arp_table(arp_text: str) -> list[ArpEntry]:
    entries: list[ArpEntry] = []
    interface_address = ""

    for line in arp_text.splitlines():
        interface_match = ARP_INTERFACE_PATTERN.match(line)
        if interface_match:
            interface_address = interface_match.group(1)
            continue

        entry_match = ARP_ENTRY_PATTERN.match(line)
        if not entry_match or not interface_address:
            continue

        entries.append(
            ArpEntry(
                interface_address=interface_address,
                ip_address=entry_match.group(1),
                mac_address=entry_match.group(2).lower(),
                entry_type=entry_match.group(3).lower(),
            )
        )

    return entries


def create_enrollment_candidates(
    arp_entries: list[ArpEntry],
    server_url: str,
    gateway_address: str,
    interval_seconds: float,
) -> list[ReceiverEnrollmentCandidate]:
    candidates: list[ReceiverEnrollmentCandidate] = []

    for entry in arp_entries:
        if not is_host_address(entry.ip_address):
            continue

        if entry.mac_address in {BROADCAST_MAC_ADDRESS, ZERO_MAC_ADDRESS}:
            continue

        classification = classify_arp_entry(entry, gateway_address)
        if classification in {GATEWAY_CLASSIFICATION, NETWORK_CONTROL_CLASSIFICATION}:
            continue

        receiver_id = create_receiver_id(entry.ip_address)
        receiver_label = create_receiver_label(entry, classification)
        candidates.append(
            ReceiverEnrollmentCandidate(
                ip_address=entry.ip_address,
                mac_address=entry.mac_address,
                vendor_hint=get_vendor_hint(entry.mac_address),
                classification=classification,
                receiver_id=receiver_id,
                receiver_label=receiver_label,
                command=create_receiver_agent_command(
                    server_url=server_url,
                    receiver_id=receiver_id,
                    receiver_label=receiver_label,
                    gateway_address=gateway_address,
                    interval_seconds=interval_seconds,
                    classification=classification,
                ),
            )
        )

    return candidates


def classify_arp_entry(entry: ArpEntry, gateway_address: str) -> str:
    if entry.ip_address == gateway_address:
        return GATEWAY_CLASSIFICATION

    if entry.entry_type == "static":
        return NETWORK_CONTROL_CLASSIFICATION

    vendor_hint = get_vendor_hint(entry.mac_address)
    if vendor_hint == "Dell":
        return WINDOWS_RECEIVER_CLASSIFICATION

    if vendor_hint == "Samsung":
        return ANDROID_RECEIVER_CLASSIFICATION

    if is_locally_administered_mac(entry.mac_address):
        return PRIVATE_MAC_CLASSIFICATION

    return GENERIC_RECEIVER_CLASSIFICATION


def create_receiver_id(ip_address: str) -> str:
    return f"{RECEIVER_ID_PREFIX}_{ip_address.replace('.', '_')}"


def create_receiver_label(entry: ArpEntry, classification: str) -> str:
    vendor_hint = get_vendor_hint(entry.mac_address)
    if vendor_hint:
        return f"{vendor_hint} {entry.ip_address}"

    return f"{classification.replace('_', ' ').title()} {entry.ip_address}"


def create_receiver_agent_command(
    server_url: str,
    receiver_id: str,
    receiver_label: str,
    gateway_address: str,
    interval_seconds: float,
    classification: str,
) -> str:
    if classification == ANDROID_RECEIVER_CLASSIFICATION:
        return "Android/appliance device: use an Android collector or confirm this device can run Python on Windows."

    return (
        "python wifi_receiver_agent.py "
        f"--server-url {quote_powershell_argument(server_url)} "
        f"--receiver-id {quote_powershell_argument(receiver_id)} "
        f"--receiver-label {quote_powershell_argument(receiver_label)} "
        f"--gateway-address {quote_powershell_argument(gateway_address)} "
        f"--interval {interval_seconds:g}"
    )


def quote_powershell_argument(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._:/-]+", value):
        return value

    escaped_value = value.replace("`", "``").replace('"', '`"')
    return f'"{escaped_value}"'


def get_vendor_hint(mac_address: str) -> str:
    return VENDOR_HINTS_BY_PREFIX.get(mac_address[:8], "")


def is_locally_administered_mac(mac_address: str) -> bool:
    first_octet = int(mac_address[:2], 16)
    return bool(first_octet & 0x02)


def is_host_address(ip_address: str) -> bool:
    try:
        parsed_address = ipaddress.ip_address(ip_address)
    except ValueError:
        return False

    return not parsed_address.is_multicast and not parsed_address.is_unspecified


def format_text_report(candidates: list[ReceiverEnrollmentCandidate]) -> str:
    if not candidates:
        return "No receiver candidates found in the current ARP table."

    lines = [
        "Remote receiver enrollment plan",
        "Run the generated command only on devices you own and intentionally enroll.",
        "",
    ]
    for candidate in candidates:
        vendor = candidate.vendor_hint or "unknown"
        lines.extend(
            [
                f"- {candidate.ip_address} · {vendor} · {candidate.classification}",
                f"  receiver_id: {candidate.receiver_id}",
                f"  command: {candidate.command}",
                "",
            ]
        )

    return "\n".join(lines).rstrip()


def discover_candidates(arguments: argparse.Namespace) -> list[ReceiverEnrollmentCandidate]:
    arp_text = run_command(["arp", "-a"])
    arp_entries = parse_arp_table(arp_text)
    return create_enrollment_candidates(
        arp_entries=arp_entries,
        server_url=arguments.server_url,
        gateway_address=arguments.gateway_address,
        interval_seconds=arguments.interval,
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a safe enrollment plan for consented WiFi receiver devices."
    )
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--gateway-address", default=DEFAULT_GATEWAY_ADDRESS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    candidates = discover_candidates(arguments)
    if arguments.json:
        print(json.dumps([asdict(candidate) for candidate in candidates], indent=2))
        return

    print(format_text_report(candidates))


if __name__ == "__main__":
    main()
