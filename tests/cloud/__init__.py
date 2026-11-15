"""Offline checks for rn-fetch.py against a fake engine database (same messages schema as Reason Network
v0.10.5 src/store.rs) and the relay's own RN-FWD encoder (agent_relay.py), plus the RN-REFWD re-forward flow
against a fake reason-network binary. Run: python3 -m unittest discover -s tests/cloud -t . -p 'test_*.py'"""
