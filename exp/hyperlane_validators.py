#!/usr/bin/env python3
"""实测 Hyperlane 的 separability：安装权限 ∩ 验证根权限 = ∅ ?

separability_check.py 里这一条是 curated（读文档断言 "not validator set"）。
本脚本把它变成实测：枚举 Mailbox 默认 ISM 树下的**全部 validator 地址**，
与 Mailbox owner Safe 的 **signer 集合**求交集。

纪律：任何 RPC 失败都记录进 unresolved，绝不静默跳过。
「查了但没有」和「没查成」在输出里必须可区分。

用法:
  export ETH_RPC_URL=https://ethereum-rpc.publicnode.com
  python3 hyperlane_validators.py
输出:
  data/hyperlane_authority_sets.json
"""
from __future__ import annotations
import json, os, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).parent
RPC = os.environ.get("ETH_RPC_URL") or sys.exit("需要 ETH_RPC_URL")
MAILBOX = "0xc005dc82818d67AF737725bD4bf75435d065D239"

# Hyperlane IInterchainSecurityModule.Types
ROUTING, AGGREGATION = 1, 2
NULL_TYPE = 6                          # PausableISM / Types.NULL — no validators
MULTISIG_TYPES = {3, 4, 5, 9, 10}     # LEGACY / MERKLE_ROOT / MESSAGE_ID / WEIGHTED x2

unresolved: list[dict] = []

def cast(addr: str, sig: str, *args: str) -> str | None:
    """失败返回 None 并登记，不抛——但调用方必须处理 None。"""
    cmd = ["cast", "call", "--rpc-url", RPC, addr, sig, *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        unresolved.append({"addr": addr, "sig": sig,
                           "err": (r.stderr or "").strip()[:200]})
        return None
    return r.stdout.strip()

def addrs(out: str | None) -> list[str]:
    if not out: return []
    return [w.lower() for w in out.replace("[", " ").replace("]", " ")
            .replace(",", " ").split() if w.startswith("0x") and len(w) == 42]

def synthetic_message() -> str:
    """最小合法 Hyperlane 消息：version+nonce+origin+sender+dest+recipient+body"""
    return "0x03" + "00000000" + "00000001" + "00"*32 + "00000001" + "00"*32

def validators_of(ism: str) -> list[str]:
    for arg in ("0x", synthetic_message()):
        out = cast(ism, "validatorsAndThreshold(bytes)(address[],uint8)", arg)
        if out: return addrs(out)
    return []

def walk(ism: str, depth: int, seen: set[str], acc: dict) -> None:
    ism = ism.lower()
    if ism in seen or depth > 6: return
    seen.add(ism)
    mt_raw = cast(ism, "moduleType()(uint8)")
    if mt_raw is None:
        acc["nodes"].append({"addr": ism, "moduleType": None, "note": "moduleType 查询失败"})
        return
    mt = int(mt_raw.split()[0])
    node = {"addr": ism, "moduleType": mt, "depth": depth}

    if mt == AGGREGATION:
        out = cast(ism, "modulesAndThreshold(bytes)(address[],uint8)", "0x")
        subs = addrs(out)
        node["kind"] = "AGGREGATION"; node["children"] = subs
        if not subs:
            unresolved.append({"addr": ism, "sig": "modulesAndThreshold",
                               "err": "返回空——聚合子模块未取到"})
        acc["nodes"].append(node)
        for m in subs: walk(m, depth+1, seen, acc)

    elif mt == ROUTING:
        doms_raw = cast(ism, "domains()(uint32[])")
        doms = [d for d in (doms_raw or "").replace("[", " ").replace("]", " ")
                .replace(",", " ").split() if d.isdigit()]
        node["kind"] = "ROUTING"; node["domains"] = doms
        if not doms:
            unresolved.append({"addr": ism, "sig": "domains()",
                               "err": "未取到 domain 列表——路由子树未展开"})
        acc["nodes"].append(node)
        for d in doms:
            sub = cast(ism, "module(uint32)(address)", d)
            for a in addrs(sub): walk(a, depth+1, seen, acc)

    elif mt in MULTISIG_TYPES:
        vs = validators_of(ism)
        node["kind"] = "MULTISIG"; node["validators"] = vs
        if not vs:
            unresolved.append({"addr": ism, "sig": "validatorsAndThreshold",
                               "err": "未取到 validator——该叶子未计入"})
        acc["validators"].update(vs)
        acc["nodes"].append(node)

    elif mt == NULL_TYPE:
        # Types.NULL / PausableISM: always-true when unpaused; no validator set.
        node["kind"] = "NULL"; node["validators"] = []
        acc["nodes"].append(node)

    else:
        node["kind"] = f"OTHER({mt})"
        acc["nodes"].append(node)
        unresolved.append({"addr": ism, "sig": "moduleType",
                           "err": f"未识别的 moduleType={mt}，未展开"})

def main() -> None:
    default_ism = cast(MAILBOX, "defaultIsm()(address)")
    if not default_ism: sys.exit("defaultIsm() 失败，无法继续")
    root = addrs(default_ism)[0]

    acc = {"nodes": [], "validators": set()}
    walk(root, 0, set(), acc)

    sep = json.load(open(HERE / "separability.json"))
    hl = [s for s in sep["stacks"] if s["name"] == "hyperlane"][0]
    signers = {a.lower() for a in hl.get("signers", [])}
    validators = acc["validators"]
    inter = sorted(signers & validators)

    out = {
        "mailbox": MAILBOX,
        "default_ism": root,
        "install_authority": {
            "safe": hl.get("owner_addr"), "threshold": hl.get("threshold"),
            "signers": sorted(signers), "n": len(signers)},
        "verification_authority": {
            "validators": sorted(validators), "n": len(validators)},
        "intersection": inter,
        "disjoint": len(inter) == 0,
        "ism_tree": acc["nodes"],
        "unresolved": unresolved,
        "measured": True,
    }
    (HERE / "data").mkdir(exist_ok=True)
    json.dump(out, open(HERE / "data/hyperlane_authority_sets.json", "w"), indent=1)

    print(f"default ISM        : {root}")
    print(f"install authority  : {len(signers)} signers (Safe {hl.get('threshold')}-of-{len(signers)})")
    print(f"verification auth. : {len(validators)} validators over {len(acc['nodes'])} ISM nodes")
    print(f"intersection       : {len(inter)}  -> {'DISJOINT ✓' if not inter else inter}")
    if unresolved:
        print(f"\n⚠️  {len(unresolved)} 处未解析（结果为下界，不可声称完备）:")
        for u in unresolved[:10]:
            print(f"   {u['addr'][:12]}… {u['sig']}: {u['err'][:90]}")
        print("   -> 修掉这些之前，不要把 disjoint 写成确定结论")
    else:
        print("\n无未解析项：validator 集合完整枚举")

if __name__ == "__main__":
    main()
