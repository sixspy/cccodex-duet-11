"""双轨时钟漂移：裁窗、旁路账册、缓冲包与批末重裁。

口径与判定在 app.modules.双轨时钟漂移 内，本模块只负责落库与并发。
关键约束：
- 装载与对齐都取 BEGIN IMMEDIATE，裁决、账册、流水同一事务成败。
- 旁路账册/缓冲包都以 (轨别, 事件编号) 唯一；重复落账被吞，不双落。
- 对齐只认 命运='已入左'/'已入右'；旁路与缓冲另册，天然不进对齐差。
"""
from __future__ import annotations

from app.db import 连接
from app.seed import 此刻
from app.services import 时间
from app.modules import 双轨时钟漂移 as 钟

口径键 = "时钟口径"
窗始键 = "窗口开始"
窗终键 = "窗口结束"

_轨流水 = {"左": "左轨流水", "右": "右轨流水"}
_已入 = {"左": "已入左", "右": "已入右"}


def _规(conn) -> dict:
    return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 轨规").fetchall()}


def _规_只读() -> dict:
    conn = 连接()
    try:
        return _规(conn)
    finally:
        conn.close()


def _取口径(规: dict) -> str:
    值 = (规.get(口径键) or 钟.口径_不裁).strip()
    return 值 if 值 in 钟.口径集 else 钟.口径_不裁


def _时刻(s: str | None):
    if not s:
        return None
    try:
        return 时间.解析(str(s).strip())
    except (ValueError, TypeError):
        return None


def _窗(规: dict):
    始 = _时刻(规.get(窗始键))
    终 = _时刻(规.get(窗终键))
    if 始 is None or 终 is None or 终 <= 始:
        return None
    return 始, 终


def 取态() -> dict:
    conn = 连接()
    try:
        规 = _规(conn)
        口径 = _取口径(规)
        窗 = _窗(规)
        return {
            "口径": 口径,
            "口径集": list(钟.口径集),
            "窗口开始": 规.get(窗始键, ""),
            "窗口结束": 规.get(窗终键, ""),
            "窗口有效": 窗 is not None,
            "窗口语义": "[开始, 结束) UTC",
            "策略卡片": 钟.策略卡片(口径),
            "采集口径": 钟.采集口径卡(),
            "旁路账册": conn.execute("SELECT COUNT(*) c FROM 时钟旁路账册").fetchone()["c"],
            "缓冲包": conn.execute("SELECT COUNT(*) c FROM 时钟缓冲包").fetchone()["c"],
            "缓冲待批末": conn.execute(
                "SELECT COUNT(*) c FROM 时钟缓冲包 WHERE 包状态=?", (钟.包_待批末,)
            ).fetchone()["c"],
            "裁决": conn.execute("SELECT COUNT(*) c FROM 时钟裁决").fetchone()["c"],
        }
    finally:
        conn.close()


def 设口径(口径: str | None = None, 窗口开始: str | None = None, 窗口结束: str | None = None,
           连带重裁: bool = True) -> dict:
    if 口径 is not None:
        钟.校验口径(口径)
    if 窗口开始 and _时刻(窗口开始) is None:
        raise ValueError("窗口开始 不是合法时间")
    if 窗口结束 and _时刻(窗口结束) is None:
        raise ValueError("窗口结束 不是合法时间")
    # 严格/缓冲必须有有效窗口；落库前就拒绝，免得存进一个裁决必驳回的口径。
    # 只改窗口时也要按「改后的口径」校验，否则会把在用的严格/缓冲拖进无窗态。
    if 口径 is not None or 窗口开始 is not None or 窗口结束 is not None:
        现 = _规_只读()
        用口径 = 口径 if 口径 is not None else _取口径(现)
        始 = 窗口开始 if 窗口开始 is not None else 现.get(窗始键)
        终 = 窗口结束 if 窗口结束 is not None else 现.get(窗终键)
        if 用口径 != 钟.口径_不裁 and _窗({窗始键: 始, 窗终键: 终}) is None:
            raise ValueError(f"{用口径} 需要有效窗口（窗口开始 < 窗口结束）")
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        改 = {}
        if 口径 is not None:
            改[口径键] = 口径
        if 窗口开始 is not None:
            改[窗始键] = 窗口开始
        if 窗口结束 is not None:
            改[窗终键] = 窗口结束
        for k, v in 改.items():
            conn.execute(
                "INSERT INTO 轨规(键, 值) VALUES (?, ?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (k, str(v)),
            )
        conn.commit()
    finally:
        conn.close()
    # 口径或窗口变了就顺手重裁存量，避免旧口径入轨行与旁路账册双落
    新口径 = 取态()["口径"]
    if 连带重裁 and (口径 is not None or 窗口开始 is not None or 窗口结束 is not None):
        try:
            重裁存量(新口径)
        except ValueError:
            pass
    return 取态()


def _裁(口径: str, 窗, 事件时: str, 处理时: str) -> dict:
    """解析两时并定命。事件时缺失或非法→驳回，绝不拿处理时顶替。"""
    if 口径 == 钟.口径_不裁:
        return {
            "命运": 钟.命_不裁, "valid": 1, "落处": 钟.落_无,
            "事件窗内": None, "处理窗内": None, "驳回": None,
        }
    e时 = _时刻(事件时)
    if e时 is None:
        return {
            "命运": 钟.命_驳非法, "valid": 0, "落处": 钟.落_无,
            "事件窗内": None, "处理窗内": None, "驳回": "事件时缺失或非法",
        }
    p时 = _时刻(处理时)
    if p时 is None:
        return {
            "命运": 钟.命_驳处理时, "valid": 0, "落处": 钟.落_无,
            "事件窗内": None, "处理窗内": None, "驳回": "处理时缺失或非法",
        }
    if 窗 is None:
        # 严格/缓冲须有有效窗口；缺窗即拒绝，避免无窗静默全裁
        return {
            "命运": 钟.命_驳非法, "valid": 0, "落处": 钟.落_无,
            "事件窗内": None, "处理窗内": None, "驳回": "窗口未配置或非法",
        }
    始, 终 = 窗
    e内 = 始 <= e时 < 终
    p内 = 始 <= p时 < 终
    命, valid, 落 = 钟.定命(口径, e内, p内)
    return {"命运": 命, "valid": valid, "落处": 落, "事件窗内": int(e内), "处理窗内": int(p内), "驳回": None}


def 裁决装载(conn, 轨别: str, 事件编号: str, 业务键: str, 金额分: int,
             事件时: str, 处理时: str, 规: dict) -> dict:
    """装载时裁决：写时钟裁决；按落处写旁路账册或缓冲包。调用方已持 IMMEDIATE 事务。

    返回 {"命运": 写入流水的命运, "入流": bool}。
    """
    口径 = _取口径(规)
    if 口径 == 钟.口径_不裁:
        # 基线 happy path 不留痕：不写裁决、不落账册，行为与空桩前一致
        return {"命运": _已入[轨别], "入流": True, "驳回": None}
    窗 = _窗(规)
    判 = _裁(口径, 窗, 事件时, 处理时)
    if 判["驳回"]:
        # 驳回不落裁决、不落账册、不写流水；由调用方计入驳回
        return {"命运": 判["命运"], "入流": False, "驳回": 判["驳回"]}

    落 = 判["落处"]
    if 落 == 钟.落_旁路:
        命运 = "旁路-时钟出窗"
    elif 落 == 钟.落_缓冲:
        命运 = "缓冲-时钟待批末"
    elif 判["命运"] == 钟.命_窗外:
        命运 = "窗外-时钟"
    else:
        命运 = _已入[轨别]

    conn.execute(
        """INSERT INTO 时钟裁决(轨别,事件编号,业务键,事件时,处理时,事件窗内,处理窗内,命运,valid,落处,口径,时刻)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(轨别,事件编号) DO UPDATE SET
             业务键=excluded.业务键, 事件时=excluded.事件时, 处理时=excluded.处理时,
             事件窗内=excluded.事件窗内, 处理窗内=excluded.处理窗内, 命运=excluded.命运,
             valid=excluded.valid, 落处=excluded.落处, 口径=excluded.口径, 时刻=excluded.时刻""",
        (轨别, 事件编号, 业务键, 事件时, 处理时, 判["事件窗内"], 判["处理窗内"],
        判["命运"], 判["valid"], 落, 口径, 此刻()),
    )
    if 落 == 钟.落_旁路:
        conn.execute(
            """INSERT OR IGNORE INTO 时钟旁路账册
               (轨别,事件编号,业务键,金额分,事件时,处理时,原因,口径,时刻)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (轨别, 事件编号, 业务键, 金额分, 事件时, 处理时, "事件时出窗、处理时入窗", 口径, 此刻()),
        )
    elif 落 == 钟.落_缓冲:
        conn.execute(
            """INSERT OR IGNORE INTO 时钟缓冲包
               (轨别,事件编号,业务键,金额分,事件时,处理时,包状态,重裁命运,口径,时刻)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (轨别, 事件编号, 业务键, 金额分, 事件时, 处理时, 钟.包_待批末, "", 口径, 此刻()),
        )
    return {"命运": 命运, "入流": True, "驳回": None}


def 批末重裁() -> dict:
    """批末按事件时重裁缓冲包。串行；包非「待批末」即跳过，杜绝旁路与现行差双落。"""
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        规 = _规(conn)
        口径 = _取口径(规)
        窗 = _窗(规)
        if 口径 != 钟.口径_缓冲:
            conn.rollback()
            return {"重裁": 0, "入窗回正": 0, "转旁路": 0, "跳过": 0,
                    "说明": f"当前口径={口径}，批末重裁只在处理时缓冲下生效"}
        行 = conn.execute(
            "SELECT * FROM 时钟缓冲包 WHERE 包状态=?", (钟.包_待批末,)
        ).fetchall()
        入窗 = 转旁 = 跳过 = 0
        for r in 行:
            判 = _裁(口径, 窗, r["事件时"], r["处理时"])
            轨别, 编号 = r["轨别"], r["事件编号"]
            键参 = (轨别, 编号)

            def 收包(状态: str, 标记: str) -> bool:
                """仅在仍是「待批末」时收口；并发下第二次会返回 False 并跳过。"""
                改 = conn.execute(
                    "UPDATE 时钟缓冲包 SET 包状态=?, 重裁命运=? WHERE 编号=? AND 包状态=?",
                    (状态, 标记, r["编号"], 钟.包_待批末),
                )
                return 改.rowcount == 1

            def 记旁路(原因: str) -> None:
                conn.execute(
                    """INSERT OR IGNORE INTO 时钟旁路账册
                       (轨别,事件编号,业务键,金额分,事件时,处理时,原因,口径,时刻)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (轨别, 编号, r["业务键"], r["金额分"], r["事件时"], r["处理时"], 原因, 口径, 此刻()),
                )
                conn.execute(
                    "UPDATE 时钟裁决 SET 命运=?, valid=0, 落处=?, 口径=?, 时刻=? WHERE 轨别=? AND 事件编号=?",
                    (判["命运"], 钟.落_旁路, 口径, 此刻(), 轨别, 编号),
                )
                conn.execute(
                    "UPDATE {表} SET 命运=? WHERE 事件编号=?".format(表=_轨流水[轨别]),
                    ("旁路-时钟出窗", 编号),
                )

            if 判["驳回"]:
                # 事件时行已坏：转旁路，不静默顶替
                if not 收包(钟.包_已出账, "出账-事件时非法"):
                    跳过 += 1
                    continue
                记旁路("批末重裁-事件时非法")
                转旁 += 1
            elif 判["valid"] == 1:
                # 重裁入窗：回正为已入，参与对齐（仍以事件时为唯一依据）
                if not 收包(钟.包_已重裁, "回正-已入"):
                    跳过 += 1
                    continue
                conn.execute(
                    "UPDATE 时钟裁决 SET 命运=?, valid=1, 落处=?, 事件窗内=1, 口径=?, 时刻=? WHERE 轨别=? AND 事件编号=?",
                    (判["命运"], 钟.落_无, 口径, 此刻(), 轨别, 编号),
                )
                conn.execute(
                    "UPDATE {表} SET 命运=? WHERE 事件编号=?".format(表=_轨流水[轨别]),
                    (_已入[轨别], 编号),
                )
                入窗 += 1
            else:
                # 仍出窗 → 转旁路账册；包置已出账，避免二次落账
                if not 收包(钟.包_已出账, "出账-仍在窗外"):
                    跳过 += 1
                    continue
                记旁路("批末重裁仍在窗外")
                转旁 += 1
        conn.commit()
        return {"重裁": len(行), "入窗回正": 入窗, "转旁路": 转旁, "跳过": 跳过,
                "口径": 口径, "时刻": 此刻()}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def 重裁存量(口径: str | None = None) -> dict:
    """切口径/改窗口后重裁存量流水，杜绝旧口径入轨行与旁路账册双落。

    每次都按目标口径重判，并核对两册：
    - 事件时出窗、处理时入窗 → 必须且只在旁路账册（严格）或缓冲包（缓冲）；
    - 事件时入窗或两时均出窗 → 两册都必须没有它。
    这样窗口变更让某行由窗外出窗回正、或由窗内变出窗，都能收敛。
    幂等：重复调用不会重复落账。
    """
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        规 = _规(conn)
        用 = 口径 or _取口径(规)
        钟.校验口径(用)
        窗 = _窗(规)
        if 用 != 钟.口径_不裁 and 窗 is None:
            raise ValueError("严格/缓冲重裁需要有效窗口（窗口开始 < 窗口结束）")
        转旁 = 转缓 = 回正 = 跳过 = 0
        for 轨别 in ("左", "右"):
            表 = _轨流水[轨别]
            for r in conn.execute(f"SELECT * FROM {表}").fetchall():
                判 = _裁(用, 窗, r["事件时"], r["处理时"])
                if 判["驳回"]:
                    跳过 += 1
                    continue
                落 = 判["落处"]
                键参 = (轨别, r["事件编号"])
                在旁 = conn.execute(
                    "SELECT 1 FROM 时钟旁路账册 WHERE 轨别=? AND 事件编号=?", 键参
                ).fetchone() is not None
                包 = conn.execute(
                    "SELECT 编号, 包状态 FROM 时钟缓冲包 WHERE 轨别=? AND 事件编号=?", 键参
                ).fetchone()
                在缓 = 包 is not None
                # 缓冲里已出账的包自带旁路行，是批末收口的稳定终态，不得被重裁回退
                已结清 = 落 == 钟.落_缓冲 and 包 is not None and 包["包状态"] == 钟.包_已出账

                # 先清掉不该在的账册，保证两册与判定一致、互不双落
                if 落 != 钟.落_旁路 and 在旁 and not 已结清:
                    conn.execute("DELETE FROM 时钟旁路账册 WHERE 轨别=? AND 事件编号=?", 键参)
                    if 落 != 钟.落_缓冲:
                        回正 += 1
                if 落 != 钟.落_缓冲 and 在缓:
                    conn.execute("DELETE FROM 时钟缓冲包 WHERE 轨别=? AND 事件编号=?", 键参)

                if 落 == 钟.落_旁路:
                    新命 = "旁路-时钟出窗"
                    if not 在旁:
                        conn.execute(
                            """INSERT INTO 时钟旁路账册
                               (轨别,事件编号,业务键,金额分,事件时,处理时,原因,口径,时刻)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (轨别, r["事件编号"], r["业务键"], r["金额分"], r["事件时"], r["处理时"],
                             "口径切换重裁-事件时出窗", 用, 此刻()),
                        )
                        转旁 += 1
                elif 落 == 钟.落_缓冲:
                    if 包 is None:
                        conn.execute(
                            """INSERT INTO 时钟缓冲包
                               (轨别,事件编号,业务键,金额分,事件时,处理时,包状态,重裁命运,口径,时刻)
                               VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (轨别, r["事件编号"], r["业务键"], r["金额分"], r["事件时"], r["处理时"],
                             钟.包_待批末, "", 用, 此刻()),
                        )
                        转缓 += 1
                        新命 = "缓冲-时钟待批末"
                    elif 包["包状态"] == 钟.包_待批末:
                        新命 = "缓冲-时钟待批末"
                    elif 包["包状态"] == 钟.包_已出账:
                        # 批末已出账：保持终态；旁路行丢了就补，不回退成待批末
                        新命 = "旁路-时钟出窗"
                        if not 在旁:
                            conn.execute(
                                """INSERT INTO 时钟旁路账册
                                   (轨别,事件编号,业务键,金额分,事件时,处理时,原因,口径,时刻)
                                   VALUES (?,?,?,?,?,?,?,?,?)""",
                                (轨别, r["事件编号"], r["业务键"], r["金额分"], r["事件时"], r["处理时"],
                                 "缓冲出账补账", 用, 此刻()),
                            )
                            转旁 += 1
                    else:
                        # 曾回正「已重裁」，如今又判出窗：重开待批末，等下次批末重裁
                        conn.execute(
                            "UPDATE 时钟缓冲包 SET 包状态=?, 重裁命运='', 口径=?, 时刻=? WHERE 编号=?",
                            (钟.包_待批末, 用, 此刻(), 包["编号"]),
                        )
                        转缓 += 1
                        新命 = "缓冲-时钟待批末"
                elif 判["命运"] == 钟.命_窗外:
                    新命 = "窗外-时钟"
                else:
                    新命 = _已入[轨别]

                命 = r["命运"] or ""
                if 命 != 新命:
                    conn.execute(
                        "UPDATE {表} SET 命运=? WHERE 事件编号=?".format(表=表),
                        (新命, r["事件编号"]),
                    )
                conn.execute(
                    """INSERT INTO 时钟裁决(轨别,事件编号,业务键,事件时,处理时,事件窗内,处理窗内,命运,valid,落处,口径,时刻)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(轨别,事件编号) DO UPDATE SET
                         业务键=excluded.业务键, 事件时=excluded.事件时, 处理时=excluded.处理时,
                         命运=excluded.命运, valid=excluded.valid, 落处=excluded.落处,
                         事件窗内=excluded.事件窗内, 处理窗内=excluded.处理窗内,
                         口径=excluded.口径, 时刻=excluded.时刻""",
                    (轨别, r["事件编号"], r["业务键"], r["事件时"], r["处理时"],
                     判["事件窗内"], 判["处理窗内"], 判["命运"], 判["valid"], 落, 用, 此刻()),
                )
        if 用 == 钟.口径_不裁:
            # 回基线：不裁口径不留裁决痕迹，避免只读接口出现陈旧行
            conn.execute("DELETE FROM 时钟裁决")
        conn.commit()
        return {"口径": 用, "转旁路": 转旁, "转缓冲": 转缓, "回正": 回正,
                "跳过": 跳过, "时刻": 此刻()}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def 裁决列表(业务键: str | None = None, 轨别: str | None = None, 限制: int = 200) -> dict:
    """只读：业务键、事件时、处理时、命运、valid；附落处与窗内标记。"""
    where, 参 = [], []
    if 业务键:
        where.append("业务键=?")
        参.append(业务键)
    if 轨别:
        where.append("轨别=?")
        参.append(轨别)
    sql = "SELECT * FROM 时钟裁决"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY 编号 DESC LIMIT ?"
    参.append(max(1, min(int(限制), 1000)))
    conn = 连接()
    try:
        行 = [dict(r) for r in conn.execute(sql, 参).fetchall()]
    finally:
        conn.close()
    return {
        "条数": len(行),
        "字段": ["业务键", "事件时", "处理时", "命运", "valid"],
        "行": [
            {
                "业务键": r["业务键"],
                "事件时": r["事件时"],
                "处理时": r["处理时"],
                "命运": r["命运"],
                "valid": r["valid"],
                "轨别": r["轨别"],
                "事件编号": r["事件编号"],
                "事件窗内": r["事件窗内"],
                "处理窗内": r["处理窗内"],
                "落处": r["落处"],
                "口径": r["口径"],
            }
            for r in 行
        ],
    }


def 旁路列表(限制: int = 200) -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM 时钟旁路账册 ORDER BY 编号 DESC LIMIT ?", (限制,)
        ).fetchall()]
    finally:
        conn.close()


def 缓冲列表(限制: int = 200) -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM 时钟缓冲包 ORDER BY 编号 DESC LIMIT ?", (限制,)
        ).fetchall()]
    finally:
        conn.close()


def 旁路计数() -> int:
    conn = 连接()
    try:
        return conn.execute("SELECT COUNT(*) c FROM 时钟旁路账册").fetchone()["c"]
    finally:
        conn.close()


def 缓冲待批末计数() -> int:
    conn = 连接()
    try:
        return conn.execute(
            "SELECT COUNT(*) c FROM 时钟缓冲包 WHERE 包状态=?", (钟.包_待批末,)
        ).fetchone()["c"]
    finally:
        conn.close()
