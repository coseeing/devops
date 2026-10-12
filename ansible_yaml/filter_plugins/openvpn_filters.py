from ipaddress import IPv4Network


def _network(value: str) -> IPv4Network:
    return IPv4Network(value, strict=False)


def cidr_network(value: str) -> str:
    return str(_network(value).network_address)


def cidr_netmask(value: str) -> str:
    return str(_network(value).netmask)


def cidr_first_host(value: str) -> str:
    """OpenVPN `server` mode assigns the first host address to itself."""
    return str(next(_network(value).hosts()))


class FilterModule:
    def filters(self) -> dict[str, object]:
        return {
            "cidr_network": cidr_network,
            "cidr_netmask": cidr_netmask,
            "cidr_first_host": cidr_first_host,
        }
