"""Runs tests/scenario.lua twice (before and after a /reload) through lupa and
writes the saved variables the way WoW does, so the manager side can read them.

    python3 tests/run_addon_test.py <addon dir> <out file>
"""
import sys
from pathlib import Path

from lupa import lua51

HERE = Path(__file__).resolve().parent


def serialize(lua, name: str) -> str:
    """Writes a global table like WoW writes SavedVariables (arrays as `value, -- [n]`)."""
    ser = lua.eval(r'''
    function(v)
        local function ser(v, ind)
            local t = type(v)
            if t == "string" then return (string.format("%q", v):gsub("\\\n", "\\n")) end
            if t == "number" then
                if v == math.floor(v) then return string.format("%d", v) end
                return string.format("%.14g", v)
            end
            if t == "boolean" then return tostring(v) end
            if t ~= "table" then return "nil" end
            local out = { "{" }
            local n = #v
            for i = 1, n do out[#out + 1] = ind .. ser(v[i], ind) .. ", -- [" .. i .. "]" end
            local keys = {}
            for k in pairs(v) do
                if not (type(k) == "number" and k >= 1 and k <= n and k == math.floor(k)) then keys[#keys + 1] = k end
            end
            table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
            for _, k in ipairs(keys) do
                local ks = type(k) == "string" and ("[" .. string.format("%q", k) .. "]") or ("[" .. tostring(k) .. "]")
                out[#out + 1] = ind .. ks .. " = " .. ser(v[k], ind) .. ","
            end
            out[#out + 1] = "}"
            return table.concat(out, "\n")
        end
        return ser(v, "")
    end''')
    return f"\n{name} = " + ser(lua.globals()[name]) + "\n"


def run(addon: Path, part: int, sv_in: Path | None, start_now=None) -> tuple[int, str, object]:
    lua = lua51.LuaRuntime(unpack_returned_tuples=True)
    g = lua.globals()
    g.ADDON_DIR = str(addon)
    g.TESTS_DIR = str(HERE)
    g.PART = part
    g.SV_IN = str(sv_in) if sv_in else None
    g.START_NOW = start_now
    failures = lua.execute(f'return dofile("{HERE / "scenario.lua"}")')
    return failures, serialize(lua, "LeeroylinkDB"), g.STUB.now


def main():
    addon, out = Path(sys.argv[1]), Path(sys.argv[2])
    f1, sv1, now = run(addon, 1, None)
    out.write_text(sv1, encoding="utf-8")
    f2, sv2, _ = run(addon, 2, out, start_now=now + 15)
    out.write_text(sv2, encoding="utf-8")
    print("addon failures:", f1 + f2)
    sys.exit(1 if f1 + f2 else 0)


if __name__ == "__main__":
    main()
