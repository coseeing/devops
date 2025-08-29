#!/usr/bin/python3
"""
Custom Ansible filters for Traefik configuration conversion
"""

import re
from typing import Any, Dict, List


class FilterModule:
    """Custom Ansible filters for Traefik configuration"""

    def filters(self):
        return {
            'apply_prefix': self.apply_prefix,
            'flatten_to_labels': self.flatten_to_labels,
            'replace_placeholders': self.replace_placeholders
        }

    def apply_prefix(self, config: Dict[str, Any], prefix: str) -> Dict[str, Any]:
        """Apply prefix to router and middleware names"""
        import copy
        data = copy.deepcopy(config)
        
        def recurse(node: Any):
            if isinstance(node, dict):
                for key in list(node.keys()):
                    val = node[key]
                    # Case 1: block of routers / middlewares (dict)
                    if key in ("routers", "middlewares") and isinstance(val, dict):
                        renamed = {f"{prefix}{k}": v for k, v in val.items()}
                        node[key] = renamed
                    
                    # Case 2: inline middlewares reference (string)
                    elif key == "middlewares" and isinstance(val, str):
                        items = [f"{prefix}{tok.strip()}" for tok in val.split(",")]
                        node[key] = ",".join(items)
                    
                    # continue traversal
                    recurse(node[key])
            elif isinstance(node, list):
                for item in node:
                    recurse(item)
        
        recurse(data)
        return data

    def flatten_to_labels(self, config: Dict[str, Any]) -> List[str]:
        """Convert nested configuration to flat label strings"""
        out = []
        
        def walk(prefix: str, node: Any):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(f"{prefix}.{k}" if prefix else k, v)
            elif isinstance(node, list):
                for i, item in enumerate(node):
                    walk(f"{prefix}[{i}]", item)
            else:  # scalar
                out.append(f"traefik.{prefix}={node}")
        
        walk("", config)
        return out

    def replace_placeholders(self, config: Dict[str, Any], domain: str, certresolver: str) -> Dict[str, Any]:
        """Replace placeholders in configuration"""
        import copy
        import json
        
        data = copy.deepcopy(config)
        config_str = json.dumps(data)
        
        # Replace placeholders
        config_str = config_str.replace("(`host`)", domain)
        config_str = config_str.replace("(`certresolver`)", certresolver)
        
        return json.loads(config_str)

