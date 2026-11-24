from app.db import 连接

def 轨规() -> dict:
    conn = 连接()
    rows = conn.execute("SELECT 键, 值 FROM 轨规").fetchall()
    conn.close()
    return {r["键"]: r["值"] for r in rows}

def 改轨规(体: dict) -> dict:
    # 时钟三键走时钟服务：切口径/改窗口必须连带重裁存量，
    # 否则旧口径入轨行会与旁路账册/缓冲包双落
    from app.services import 时钟
    钟键 = ("时钟口径", "窗口开始", "窗口结束")
    钟改 = {k: v for k, v in (体 or {}).items() if k in 钟键}
    余 = {k: v for k, v in (体 or {}).items() if k not in 钟键}
    # 先走时钟服务：它先校验后落库，非法值在这里就抛，其余键不会被写半截
    if 钟改:
        时钟.设口径(
            口径=钟改.get("时钟口径"),
            窗口开始=钟改.get("窗口开始"),
            窗口结束=钟改.get("窗口结束"),
        )
    if 余:
        conn = 连接()
        for k, v in 余.items():
            conn.execute(
                "INSERT INTO 轨规(键, 值) VALUES (?, ?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (k, str(v)),
            )
        conn.commit()
        conn.close()
    return 轨规()

def 源列表() -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 源登记 ORDER BY 轨别, 源键").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 左列表(限制: int = 300) -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 左轨流水 ORDER BY 处理时 DESC LIMIT ?", (限制,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 右列表(限制: int = 300) -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 右轨流水 ORDER BY 处理时 DESC LIMIT ?", (限制,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 差列表() -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 对齐差 ORDER BY 编号 DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 悬列表() -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 悬账单 ORDER BY 编号 DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 结论列表() -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 对拍结论 ORDER BY 编号 DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 概览() -> dict:
    conn = 连接()
    g = 轨规()
    out = {
        "站名": g.get("站名", "对拍"),
        "骨架": g.get("骨架", ""),
        "皮": g.get("皮", ""),
        "去皮": g.get("去皮", ""),
        "左笔数": conn.execute("SELECT COUNT(*) AS c FROM 左轨流水").fetchone()["c"],
        "右笔数": conn.execute("SELECT COUNT(*) AS c FROM 右轨流水").fetchone()["c"],
        "对齐差数": conn.execute("SELECT COUNT(*) AS c FROM 对齐差").fetchone()["c"],
        "悬账数": conn.execute("SELECT COUNT(*) AS c FROM 悬账单").fetchone()["c"],
        "结论数": conn.execute("SELECT COUNT(*) AS c FROM 对拍结论").fetchone()["c"],
        "时钟旁路数": conn.execute("SELECT COUNT(*) AS c FROM 时钟旁路账册").fetchone()["c"],
        "时钟缓冲待批末": conn.execute(
            "SELECT COUNT(*) AS c FROM 时钟缓冲包 WHERE 包状态='待批末'"
        ).fetchone()["c"],
        "时钟口径": g.get("时钟口径", "不裁"),
    }
    conn.close()
    return out
