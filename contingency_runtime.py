"""Runtime contract for the temporary, unpriced board (not a Sofa bypass)."""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path


def forced_odds_only():
    return os.environ.get("MESA_ODDS_ONLY", "").strip().lower() in ("1", "true", "yes")


def contingency_reasons():
    # Empty coverage checks ONLY source integrity/freshness, not the resulting board.
    from gate_board import load_sofa_state, sofa_reasons
    if forced_odds_only():
        return ["Contingência ativa até a retomada auditada da agenda Sofascore"]
    return sofa_reasons({"n_games": 0, "value_games": 0}, load_sofa_state({}))


def _aware(value):
    if not isinstance(value, str):
        raise ValueError("timestamp ausente")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("timestamp sem fuso")
    return dt


def public_odds_only_reasons(board, now=None):
    """Public smoke contract. Does NOT replace verification against raw snapshots."""
    now = now or datetime.now(timezone.utc)
    errors = []
    try:
        if board.get("mode") != "odds_only":
            raise ValueError("modo não é odds_only")
        if board.get("model") != {"status": "unavailable", "source": "none", "markets": []}:
            raise ValueError("modelo não foi desativado")
        if board.get("pricing") != {}:
            raise ValueError("pricing deve estar vazio")
        captured = _aware(board.get("gerado_iso"))
        rebuilt = _aware(board.get("rebuilt_at"))
        expiry = _aware(board["contingency"]["expires_at"])
        if not now - timedelta(minutes=120) <= captured <= now + timedelta(minutes=5):
            raise ValueError("captura fora do TTL de 120 minutos")
        if not captured <= rebuilt <= now + timedelta(minutes=5):
            raise ValueError("relógio de reconstrução inválido")
        if not now < expiry <= captured + timedelta(minutes=120):
            raise ValueError("contingência expirada ou TTL ampliado")
        games = board.get("jogos")
        if not isinstance(games, list) or not games:
            raise ValueError("nenhuma oferta verificada")
        for game in games:
            if game.get("valor") != [] or game.get("tem_valor") is not False or game.get("sofa_id"):
                raise ValueError("sinal de modelo/identidade Sofa indevido")
            cap = _aware(game.get("captured_at"))
            end = _aware(game.get("expires_at"))
            kickoff = _aware(game.get("inicio_iso"))
            if not captured <= cap <= now + timedelta(minutes=5):
                raise ValueError("captura do jogo inválida")
            if not now < end <= cap + timedelta(minutes=120) or expiry > end:
                raise ValueError("oferta expirada ou TTL do jogo ampliado")
            if kickoff <= now or game.get("game_state") != "upcoming":
                raise ValueError("jogo não é pré-live")
        if min(_aware(g["captured_at"]) for g in games) != captured:
            raise ValueError("frescor não representa a captura mais antiga")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        errors.append("odds_only: " + str(exc))
    return errors


def refresh_before_persist(root=None):
    """After any reconciliation, rebuild proof using CURRENT pointers; no history rebuild."""
    root = Path(root or Path(__file__).resolve().parent)
    from gate_board import parse_board
    path = root / "valor/data/board.js"
    board = parse_board(path.read_text(encoding="utf-8"))
    if board.get("mode") != "odds_only":
        if forced_odds_only():
            raise ValueError("contingência obrigatória: recuso persistir board completo")
        return False
    from odds_only import build_odds_only, odds_only_reasons
    from capture_common import _atomic_write_text
    rebuilt = build_odds_only(root, reason=board["contingency"]["reason"])
    reasons = public_odds_only_reasons(rebuilt) + odds_only_reasons(rebuilt, root)
    if reasons:
        raise ValueError("; ".join(reasons))
    _atomic_write_text(path, "window.BOARD=" + json.dumps(rebuilt, ensure_ascii=False) + ";")
    return True


if __name__ == "__main__":
    import subprocess
    import sys
    if refresh_before_persist():
        # All other artifacts retain their existing history integrity contract.
        for script in ("build_ops.py", "build_manifest.py"):
            subprocess.run([sys.executable, str(Path(__file__).resolve().parent / script)], check=True)
