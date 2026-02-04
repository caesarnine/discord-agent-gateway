#!/usr/bin/env python3
"""
Discord Agent Gateway (legacy entrypoint)

Prefer:
  python -m discord_agent_gateway

This file exists as a thin wrapper so local setups can keep using:
  python agent_gateway.py
"""

from discord_agent_gateway.cli import main


if __name__ == "__main__":
    main()

