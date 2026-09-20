"""Software book separation on primary account 0; never transfers account funds."""
from decimal import Decimal

from .contracts import DeskError, utcnow
from .exchange import KalshiDeskExchange, _decimal


class SharedAccountExchange(KalshiDeskExchange):
    def __init__(self, base_url, key_id, private_key, *, ownership, store, desk_id, client=None):
        super().__init__(base_url, key_id, private_key, 0, client=client, shared_primary=True)
        self.ownership, self.store, self.desk_id = ownership, store, desk_id

    def _rows(self, ticker, collection):
        path, key = {"orders": ("orders", "orders"),
                     "positions": ("positions", "market_positions")}[collection]
        rows = list(self._pages("/portfolio/" + path, key, {"subaccount": 0, "ticker": ticker}))
        for row in rows:
            if (row.get("ticker") or row.get("market_ticker")) != ticker:
                raise DeskError("shared_market_identity_mismatch")
            account = row.get("subaccount_number", 0)
            if type(account) is not int or account != 0:
                raise DeskError("shared_account_identity_mismatch")
        return rows

    def prepare_market(self, ticker):
        owner = self.ownership.owner(ticker)
        if owner is None:
            # Existing account activity belongs to the incumbent, never the desks.
            if self._rows(ticker, "orders") or any(
                    _decimal(r.get("position_fp")) != 0 for r in self._rows(ticker, "positions")):
                self.ownership.claim(ticker, "main")
                raise DeskError("market_has_existing_account_activity")
        try:
            self.ownership.claim(ticker, self.desk_id)
        except ValueError as exc:
            raise DeskError("market_owned_by_another_book") from exc
        self.audit()

    def audit(self):
        records = [r for r in self.store.snapshot(utcnow())["decisions"] if r["desk_id"] == self.desk_id]
        for ticker in self.ownership.desk_markets():
            if self.ownership.owner(ticker) != self.desk_id:
                continue
            known = [r for r in records if r["ticker"] == ticker]
            ids = {r["client_order_id"] for r in known}
            if any(r.get("client_order_id") not in ids for r in self._rows(ticker, "orders")):
                raise DeskError("shared_account_unattributed_order")
            # Pending fills are reconciled first; their unknown quantity cannot
            # be guessed or treated as zero. New submission remains blocked.
            if any(r["status"] in {"submitting", "pending", "unknown"} for r in known):
                raise DeskError("shared_account_pending_reconciliation")
            for record in known:
                if record["status"] != "terminal" or record["settled"]:
                    continue
                report = self.reconcile(record["client_order_id"], ticker)
                if report.status != "terminal" or any(
                        getattr(report, key) != Decimal(record[key])
                        for key in ("filled_quantity", "fill_cost", "fees")):
                    raise DeskError("shared_account_fill_mismatch")
            positions = self._rows(ticker, "positions")
            actual = sum((_decimal(r.get("position_fp")) for r in positions), Decimal(0))
            expected = sum((Decimal(r["filled_quantity"]) * (1 if r["payload"]["side"] == "yes" else -1)
                            for r in known if not r["settled"]), Decimal(0))
            if actual != expected:
                if actual != 0 or self.settlement(ticker) is None:
                    raise DeskError("shared_account_position_mismatch")

    def check_isolation(self):
        self.audit()
        balance = _decimal(self._request("GET", "/portfolio/balance", params={"subaccount": 0}).get("balance")) / 100
        # Allocations are liabilities of this pooled account, not deposits.
        books = self.store.snapshot(utcnow())["desks"]
        required = sum((Decimal(b["available_cash"]) for b in books), Decimal(0))
        if balance < required:
            raise DeskError("shared_account_cash_shortfall")
        return {"verified": True, "balance": str(balance), "subaccount": 0,
                "protection": "software_ownership", "checked_at": utcnow().isoformat()}

    def check_clean_book(self):
        self.audit()
        return {"clean": True, "subaccount": 0, "scope": "owned_markets_only"}

    def submit_ioc(self, client_order_id, ticker, side, quantity, limit_price):
        if self.ownership.owner(ticker) != self.desk_id:
            raise DeskError("market_owned_by_another_book")
        return super().submit_ioc(client_order_id, ticker, side, quantity, limit_price)
