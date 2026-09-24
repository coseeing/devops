#!/usr/bin/env python3
"""Validate and normalize inputs used by the OpenVPN deployment."""

import argparse
import json
import re
import sys
from ipaddress import IPv4Address, IPv4Network


LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
RFC1918_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)


def endpoint(value: str) -> str:
    try:
        IPv4Address(value)
        return value
    except ValueError:
        labels = value.rstrip(".").split(".")
        if (
            len(value) <= 253
            and len(labels) >= 2
            and all(LABEL.fullmatch(label) for label in labels)
        ):
            return value.rstrip(".").lower()
    raise ValueError("endpoint must be a hostname or IPv4 address without a port")


class ArgumentParser(argparse.ArgumentParser):
    """Keep CLI failures to the documented single error line."""

    def error(self, message: str) -> None:
        self.exit(2, f"error: {message}\n")


def parser() -> argparse.ArgumentParser:
    result = ArgumentParser()
    result.add_argument("--endpoint", required=True)
    result.add_argument("--vpn-cidr", required=True)
    result.add_argument("--windows-cidr", required=True)
    result.add_argument("--vpc-cidr", required=True)
    result.add_argument("--client-days", required=True, type=int)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        public_endpoint = endpoint(args.endpoint)
        vpn = IPv4Network(args.vpn_cidr, strict=False)
        windows = IPv4Network(args.windows_cidr, strict=False)
        vpc = IPv4Network(args.vpc_cidr, strict=False)
        if not any(vpn.subnet_of(private) for private in RFC1918_NETWORKS):
            raise ValueError("VPN CIDR must be a private IPv4 network")
        if not 16 <= vpn.prefixlen <= 29:
            raise ValueError("VPN CIDR prefix length must be between /16 and /29")
        if vpn.overlaps(vpc):
            raise ValueError("VPN CIDR overlaps VPC CIDR")
        if not windows.subnet_of(vpc):
            raise ValueError("Windows CIDR is not inside VPC CIDR")
        if vpn.overlaps(windows):
            raise ValueError("VPN CIDR overlaps Windows CIDR")
        if not 1 <= args.client_days <= 365:
            raise ValueError("client-days must be between 1 and 365")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "client_days": args.client_days,
                "endpoint": public_endpoint,
                "vpn_cidr": str(vpn),
                "vpc_cidr": str(vpc),
                "windows_cidr": str(windows),
                "windows_netmask": str(windows.netmask),
                "windows_network": str(windows.network_address),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
