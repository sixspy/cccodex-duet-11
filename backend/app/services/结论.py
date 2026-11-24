"""对拍结论：核对对齐差与悬账是否自洽。"""
from __future__ import annotations
from app.db import 连接
from app.seed import 此刻

def 跑一次() -> dict:
    conn = 连接()
    try:
        左 = conn.execute("SELECT COUNT(*) AS c FROM 左轨流水").fetchone()["c"]
        右 = conn.execute("SELECT COUNT(*) AS c FROM 右轨流水").fetchone()["c"]
        对齐 = conn.execute(
            "SELECT COUNT(*) AS c FROM 对齐差 WHERE 差型='对齐'"
        ).fetchone()["c"]
        悬 = conn.execute("SELECT COUNT(*) AS c FROM 悬账单").fetchone()["c"]
        差总 = conn.execute("SELECT COUNT(*) AS c FROM 对齐差").fetchone()["c"]

        问题 = []
        # 悬账单业务键应能在对齐差找到左缺/右缺
        for r in conn.execute("SELECT * FROM 悬账单").fetchall():
            行 = conn.execute(
                "SELECT 差型 FROM 对齐差 WHERE 业务键=?", (r["业务键"],)
            ).fetchone()
            if not 行:
                问题.append(f"悬账{r['业务键']}无对齐差行")
            elif r["悬边"] == "左" and 行["差型"] not in ("右缺", "金额不符"):
                问题.append(f"左悬{r['业务键']}差型={行['差型']}")
            elif r["悬边"] == "右" and 行["差型"] not in ("左缺", "金额不符"):
                问题.append(f"右悬{r['业务键']}差型={行['差型']}")

        # 时钟口径非「不裁」时加核：入轨行必须 valid=1，且不得在旁路/缓冲另有落处
        口径 = conn.execute("SELECT 值 FROM 轨规 WHERE 键='时钟口径'").fetchone()
        口径 = (口径["值"] if 口径 else "不裁") or "不裁"
        旁 = conn.execute("SELECT COUNT(*) c FROM 时钟旁路账册").fetchone()["c"]
        缓 = conn.execute("SELECT COUNT(*) c FROM 时钟缓冲包 WHERE 包状态='待批末'").fetchone()["c"]
        if 口径 != "不裁":
            # 双落只算「待批末」的在途包：已出账的包留在账上是审计留痕，
            # 它与旁路账册是同一笔的先后两态，不算两边各落一处
            双落 = conn.execute(
                """SELECT COUNT(*) c FROM 时钟旁路账册 b
                   JOIN 时钟缓冲包 p ON p.轨别=b.轨别 AND p.事件编号=b.事件编号
                   WHERE p.包状态='待批末'"""
            ).fetchone()["c"]
            if 双落:
                问题.append(f"旁路与缓冲双落{双落}")
            越界 = conn.execute(
                """SELECT COUNT(*) c FROM 时钟裁决 d
                   JOIN 左轨流水 l ON l.事件编号=d.事件编号 AND d.轨别='左'
                   WHERE d.valid=0 AND l.命运 LIKE '已入%'
                   UNION ALL
                   SELECT COUNT(*) FROM 时钟裁决 d
                   JOIN 右轨流水 r ON r.事件编号=d.事件编号 AND d.轨别='右'
                   WHERE d.valid=0 AND r.命运 LIKE '已入%'"""
            ).fetchall()
            if sum(x["c"] for x in 越界):
                问题.append("有 valid=0 的事件仍按已入参与对齐")

        通过 = len(问题) == 0 and (左 + 右 == 0 or 差总 > 0 or (左 > 0 and 右 > 0 and 对齐 >= 0))
        if 左 == 0 and 右 == 0:
            通过 = True
            说明 = "双轨皆空，结论空跑通过"
        elif 问题:
            通过 = False
            说明 = "问题:" + ";".join(问题[:6])
        else:
            说明 = f"左{左}/右{右}/对齐{对齐}/悬{悬}/差行{差总} 自洽"
        通过 = False if 问题 else 通过
        if 口径 != "不裁":
            说明 += f"；时钟口径{口径}，旁路{旁} 缓冲待批末{缓}"

        conn.execute(
            """INSERT INTO 对拍结论(时刻,左笔数,右笔数,对齐笔数,悬账笔数,是否通过,说明)
               VALUES (?,?,?,?,?,?,?)""",
            (此刻(), 左, 右, 对齐, 悬, 1 if 通过 else 0, 说明),
        )
        conn.commit()
        return {
            "是否通过": 通过,
            "左笔数": 左,
            "右笔数": 右,
            "对齐笔数": 对齐,
            "悬账笔数": 悬,
            "说明": 说明,
            "问题": 问题,
            "时钟口径": 口径,
            "时钟旁路": 旁,
            "时钟缓冲待批末": 缓,
        }
    finally:
        conn.close()
