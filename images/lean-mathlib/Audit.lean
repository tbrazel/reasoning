/- Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0 -/
import Lean

open Lean

def main (args : List String) : IO UInt32 := do
  if args == ["--help"] then
    IO.println "Audit a compiled theorem with imported-module checking and no native extensions."
    return 0
  if args.length != 2 then
    IO.eprintln "Expected module and theorem name"
    return 2
  initSearchPath (← findSysroot)
  let env ← importModules #[{ module := args[0]!.toName }] {} 0 (loadExts := false)
  let name := args[1]!.toName
  let some (.thmInfo _) := env.find? name | do
    IO.eprintln "Target must be a theorem declaration"
    return 1
  let (names, _) ← (collectAxioms name : CoreM (Array Name)).toIO
    { fileName := "<reasoning-audit>", fileMap := FileMap.ofString "" } { env := env }
  let axioms := names.map Name.toString
  let allowed := #["propext", "Classical.choice", "Quot.sound"]
  let accepted := axioms.all allowed.contains
  IO.println <| Json.compress <| Json.mkObj [
    ("axioms", toJson axioms), ("accepted", toJson accepted),
    ("trust_level", toJson (0 : Nat)), ("native_extensions", toJson false)]
  return if accepted then 0 else 1
