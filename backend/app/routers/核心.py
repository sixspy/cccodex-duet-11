from fastapi import APIRouter, HTTPException
from app.services import 目录, 左装载, 右装载, 对齐, 结论, 夹具, 统计, 说明, 时钟
from app.modules import 双轨时钟漂移 as 钟

路由 = APIRouter()

@路由.get("/api/概览")
def api_概览():
    return 目录.概览()

@路由.get("/api/轨规")
def api_轨规():
    return 目录.轨规()

@路由.post("/api/轨规")
def api_改轨规(体: dict):
    try:
        return 目录.改轨规(体)
    except ValueError as e:
        # 时钟口径/窗口非法或缺失时不落半截，直接 400
        raise HTTPException(400, str(e))

@路由.get("/api/源登记")
def api_源():
    return 目录.源列表()

@路由.get("/api/左轨")
def api_左():
    return 目录.左列表()

@路由.get("/api/右轨")
def api_右():
    return 目录.右列表()

@路由.get("/api/对齐差")
def api_差():
    return 目录.差列表()

@路由.get("/api/悬账单")
def api_悬():
    return 目录.悬列表()

@路由.get("/api/对拍结论")
def api_结论列表():
    return 目录.结论列表()

@路由.get("/api/夹具")
def api_夹具():
    return 夹具.列表()

@路由.post("/api/左轨/装载")
def api_左装(体: dict):
    return 左装载.装一批(体)

@路由.post("/api/右轨/装载")
def api_右装(体: dict):
    return 右装载.装一批(体)

# 路径参数名须 ASCII：Starlette 只识别 ASCII 占位符，中文名会被当成字面量
@路由.post("/api/左轨/夹具/{fixture}")
def api_左夹(fixture: str):
    try:
        return 左装载.装一批(夹具.读取(fixture))
    except FileNotFoundError:
        raise HTTPException(404, fixture)

@路由.post("/api/右轨/夹具/{fixture}")
def api_右夹(fixture: str):
    try:
        return 右装载.装一批(夹具.读取(fixture))
    except FileNotFoundError:
        raise HTTPException(404, fixture)

@路由.post("/api/对齐/跑")
def api_对齐():
    return 对齐.跑对齐()

@路由.post("/api/对拍结论/跑")
def api_结论():
    return 结论.跑一次()


@路由.get("/api/时钟漂移")
def api_时钟态():
    return 时钟.取态()


@路由.post("/api/时钟漂移/口径")
def api_时钟口径(体: dict):
    try:
        return 时钟.设口径(
            口径=体.get("口径"),
            窗口开始=体.get("窗口开始"),
            窗口结束=体.get("窗口结束"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@路由.get("/api/时钟漂移/裁决")
def api_时钟裁决(业务键: str | None = None, 轨别: str | None = None, 限制: int = 200):
    return 时钟.裁决列表(业务键=业务键, 轨别=轨别, 限制=限制)


@路由.get("/api/时钟漂移/旁路")
def api_时钟旁路():
    return 时钟.旁路列表()


@路由.get("/api/时钟漂移/缓冲")
def api_时钟缓冲():
    return 时钟.缓冲列表()


@路由.post("/api/时钟漂移/批末重裁")
def api_时钟重裁():
    return 时钟.批末重裁()


@路由.post("/api/时钟漂移/存量重裁")
def api_时钟存量重裁(体: dict | None = None):
    try:
        return 时钟.重裁存量((体 or {}).get("口径"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@路由.get("/api/时钟漂移/策略卡")
def api_时钟策略卡():
    态 = 时钟.取态()
    return {"当前口径": 态["口径"], "卡片": 钟.策略卡片(态["口径"])}


@路由.get("/api/统计/左命运")
def api_左命运():
    return 统计.左命运()

@路由.get("/api/统计/右命运")
def api_右命运():
    return 统计.右命运()

@路由.get("/api/统计/差型")
def api_差型():
    return 统计.差型分布()

@路由.get("/api/统计/悬边")
def api_悬边():
    return 统计.悬边分布()


@路由.get("/api/仓情")
def api_仓情():
    return 说明.仓情()

@路由.get("/api/空桩")
def api_空桩():
    from pathlib import Path
    import importlib
    root = Path(__file__).resolve().parents[1] / "modules"
    out, 已落 = [], []
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if p.is_dir() and p.name != "__pycache__" and (p / "__init__.py").is_file():
                try:
                    m = importlib.import_module(f"app.modules.{p.name}")
                    已实现 = bool(getattr(m, "已实现", False))
                except Exception:
                    已实现 = False
                (已落 if 已实现 else out).append(p.name)
    return {"空桩": out, "条数": len(out), "已落": 已落}

