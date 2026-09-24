from ipaddress import IPv4Network


def _network(value: str) -> IPv4Network:
    return IPv4Network(value, strict=False)


def cidr_network(value: str) -> str:
    return str(_network(value).network_address)


def cidr_netmask(value: str) -> str:
    return str(_network(value).netmask)


class FilterModule:
    def filters(self) -> dict[str, object]:
        return {"cidr_network": cidr_network, "cidr_netmask": cidr_netmask}
