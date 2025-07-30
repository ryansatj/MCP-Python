# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx",
#     "mcp[cli]",
# ]
# ///

import random
import string
import httpx
from mcp.server.fastmcp import FastMCP
import math
from typing import cast
import math
import httpx
import os
import re
from dotenv import load_dotenv

mcp = FastMCP("zabbix")

load_dotenv()

ZABBIX_API_URL   = cast(str, os.getenv("ZABBIX_API_URL"))
ZABBIX_AUTH_TOKEN = cast(str, os.getenv("ZABBIX_AUTH_TOKEN"))

@mcp.tool()
async def get_host_problems(hostname: str) -> list:
    """
    Get list of active problems (triggers) for a given Zabbix host name use this when the user asks for problems/all problems related to a specific host.
    IMPORTANT: This tool can also be called when the user asks for to get all problems in a host, but only if the user provides a specific hostname.
    ✅ Use this ONLY when the user asks about problems/triggers on a specific host.

    Args:
        hostname (str): **MUST be passed exactly as provided by the user**. 
        DO NOT modify the string. 
        DO NOT replace spaces with underscores. 
        DO NOT change casing or add formatting.

        ✅ For example, if the user says: 'L2 Cisco 2960X IB_1F', pass exactly that.
        ❌ DO NOT convert it to: 'L2_Cisco_2960X_IB_1F'

        Important:

    Returns:
        list: List of active trigger / problems, or empty list if none, Return as points to user, Also give config fox suggestions if prompted by the user.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(ZABBIX_API_URL, json={
            "jsonrpc": "2.0",
            "method": "host.get",
            "params": {
                "output": ["hostid"],
                "filter": {"host": hostname}
            },
            "auth": ZABBIX_AUTH_TOKEN,
            "id": 1
        })
        resp.raise_for_status()
        hosts = resp.json().get("result", [])
        if not hosts:
            return [] 

        host_id = hosts[0]["hostid"]

        resp = await client.post(ZABBIX_API_URL, json={
            "jsonrpc": "2.0",
            "method": "trigger.get",
            "params": {
                "output": ["description", "priority", "value"],
                "hostids": [host_id],
                "filter": {"value": 1},
                "expandDescription": True,
                "only_true": True,
                "skipDependent": True,
                "sortfield": "priority",
                "sortorder": "DESC"
            },
            "auth": ZABBIX_AUTH_TOKEN,
            "id": 2
        })
        resp.raise_for_status()
        triggers = resp.json().get("result", [])

        return ["\n".join(f"- {t['description']}" for t in triggers)]
    
@mcp.tool()
async def get_all_problems() -> list:
    """
    Get all active problems across all hosts in Zabbix, only calls this if the user prompts to get all the problems in network.
    IMPORTANT: This tool is only for use when the user specifically asks for all problems e.g. "Get all problems in network", else dont use this ESPECIALLY IF THE USER PROVIDE HOST DONT USE THIS.

    Returns:
        list: Single-element list containing all problem descriptions formatted as a bullet list.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(ZABBIX_API_URL, json={
            "jsonrpc": "2.0",
            "method": "trigger.get",
            "params": {
                "output": ["description", "priority", "value"],
                "filter": {
                    "value": 1 
                },
                "expandDescription": True,
                "only_true": True,
                "skipDependent": True,
                "sortfield": "priority",
                "sortorder": "DESC"
            },
            "auth": ZABBIX_AUTH_TOKEN,
            "id": 1
        })
        resp.raise_for_status()
        triggers = resp.json().get("result", [])

        return ["\n".join(f"- {t['description']}" for t in triggers)]


@mcp.tool()
async def get_host_health_metrics(hostname: str) -> list:
    """
    Get SNMP-based health metrics for a Zabbix host, such as CPU usage, temperature, memory, etc. 
    ❗This tool provides SNMP-based health status (temperature, memory, CPU) only. 
    ❌ DO NOT call this if the user asks for problems or triggers — use `get_host_problems` instead

    Args:
        hostname (str): **MUST be passed exactly as provided by the user**. 
        DO NOT modify casing, spaces, or formatting.

    Returns:
        list: List of formatted metrics (CPU, Temp, Memory, etc.) or empty list if none found.
    """
    async with httpx.AsyncClient() as client:
        # Step 1: Get host ID
        resp = await client.post(ZABBIX_API_URL, json={
            "jsonrpc": "2.0",
            "method": "host.get",
            "params": {
                "output": ["hostid"],
                "filter": {"host": hostname}
            },
            "auth": ZABBIX_AUTH_TOKEN,
            "id": 1
        })
        resp.raise_for_status()
        hosts = resp.json().get("result", [])
        if not hosts:
            return []

        host_id = hosts[0]["hostid"]

        metric_keys = [
            "Temperature",
            "Memory",
            "CPU Utilization",
        ]

        result = []

        for key in metric_keys:
            resp = await client.post(ZABBIX_API_URL, json={
                "jsonrpc": "2.0",
                "method": "item.get",
                "params": {
                    "output": ["name", "lastvalue", "units"],
                    "hostids": [host_id],
                    "search": {
                        "name": key
                    },
                    "sortfield": "name"
                },
                "auth": ZABBIX_AUTH_TOKEN,
                "id": 2
            })
            resp.raise_for_status()
            items = resp.json().get("result", [])
            
            for item in items:
                formatted = f"- {item['name']}: {item['lastvalue']} {item.get('units', '')}".strip()
                result.append(formatted)

        combined_text = "\n".join(result)

        return [combined_text]
    

@mcp.tool()
async def get_host_interfaces(hostname: str) -> list:
    """
    Get all interfaces on one host on zabbix, Provide every interfaces to the user.

    Args:
        hostname (str): **MUST be passed exactly as provided by the user**. 
        DO NOT modify casing, spaces, or formatting
        Return all the interfaces this tool returns to the user do not summarize it.

    Returns:
        list: List of formatted interfaces on one host/device or empty list if none found.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(ZABBIX_API_URL, json={
            "jsonrpc": "2.0",
            "method": "host.get",
            "params": {
                "output": ["hostid"],
                "filter": {"host": hostname}
            },
            "auth": ZABBIX_AUTH_TOKEN,
            "id": 1
        })
        resp.raise_for_status()
        hosts = resp.json().get("result", [])
        if not hosts:
            return []

        host_id = hosts[0]["hostid"]

        metric_keys = [
            "Interface type",
        ]

        result = []

        for key in metric_keys:
            resp = await client.post(ZABBIX_API_URL, json={
                "jsonrpc": "2.0",
                "method": "item.get",
                "params": {
                    "output": ["name", "lastvalue", "units"],
                    "hostids": [host_id],
                    "search": {
                        "name": key
                    },
                    "sortfield": "name"
                },
                "auth": ZABBIX_AUTH_TOKEN,
                "id": 2
            })
            resp.raise_for_status()
            items = resp.json().get("result", [])
            
            for item in items:
                formatted = f"- {item['name']}: {item['lastvalue']} {item.get('units', '')}".strip()
                result.append(formatted)

        combined_text = "\n".join(result)

        return [combined_text]

@mcp.tool()
async def get_interface_info(hostname: str, interface: str) -> list:
    """
    Get all metrics related to a specific interface on a Zabbix host.

    Args:
        IMPORTANT_NOTE: add () if the user forgot to put it. But if its have (TO_IB-101 area) then dont add ().
        hostname (str): The hostname as provided by the user.
        interface (str): Interface name (e.g., Gi1/0/1). Append '()' for exact matching.
        DO NOT modify the string. 
        DO NOT replace spaces with underscores. 
        DO NOT change casing or add formatting.

        ✅ For example, if the user says: 'L2 Cisco 2960X IB_1F', pass exactly that.
        ❌ DO NOT convert it to: 'L2_Cisco_2960X_IB_1F'

    Returns:
        list: List of formatted metrics for the given interface, or empty if not found, say no interface found if this tools return empty string.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(ZABBIX_API_URL, json={
            "jsonrpc": "2.0",
            "method": "host.get",
            "params": {
                "output": ["hostid"],
                "filter": {"host": hostname}
            },
            "auth": ZABBIX_AUTH_TOKEN,
            "id": 1
        })
        resp.raise_for_status()
        hosts = resp.json().get("result", [])
        if not hosts:
            return []

        host_id = hosts[0]["hostid"]

        # Step 2: Get all items related to the specific interface
        search_string = interface
        resp = await client.post(ZABBIX_API_URL, json={
            "jsonrpc": "2.0",
            "method": "item.get",
            "params": {
                "output": ["name", "lastvalue", "units"],
                "hostids": [host_id],
                "search": {
                    "name": search_string
                },
                "sortfield": "name"
            },
            "auth": ZABBIX_AUTH_TOKEN,
            "id": 2
        })
        resp.raise_for_status()
        items = resp.json().get("result", [])

        result = []
        for item in items:
                formatted = f"- {item['name']}: {item['lastvalue']} {item.get('units', '')}".strip()
                result.append(formatted)

        combined_text = "\n".join(result)

        return [combined_text]


if __name__ == "__main__":
    mcp.run(transport="stdio")
    
