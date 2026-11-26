"""Invoke the existing live OpenSTAAD adapter."""


async def send_to_staad(data: dict) -> dict:
    """Run live OpenSTAAD; execution errors must never become simulated passes."""
    from radiator_support.staad_integration import send_to_staad as live_send
    return await live_send(data)
