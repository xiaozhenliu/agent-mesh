"""Offline checks for agent_relay.py: envelope verification (RN-OUT v1), replay/staleness/address guards, outbound
send records (duplicate, failed and unknown sends), wake limit, spool overflow, forwarding (RN-FWD v1) and doorbells,
re-forward (RN-REFWD v1), per-message isolation, crash-safe ordering and startup recovery from a throwaway
network.db. No network beyond loopback test servers, no real webhook, no reason-network binary.
Run: python3 -m unittest discover -s tests/relay -t . -p 'test_*.py'"""
