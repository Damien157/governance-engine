# hais_main.py
import asyncio
from hais_os import GovernedDecisionEngine  # if you rename your file to hais_os.py


async def main():
    engine = GovernedDecisionEngine()

    token = engine.security.generate_token("alice", "operator", ttl=300)

    intent = {"text": "contact me at test@example.com about healthcare"}
    decision = await engine.decide(intent, token=token)

    print("Decision:", decision["decision"])
    print("Risk:", decision["risk_signal"])
    print("Reasons:", decision["policy_reasons"])
    print("Audit ID:", decision["audit_id"])

    print("\nHealth:", engine.health())
    print("\nAudit chain:", engine.audit.verify_chain())


if __name__ == "__main__":
    asyncio.run(main())
