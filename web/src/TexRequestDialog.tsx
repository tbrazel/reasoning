// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import React, { useState } from "react";
import { ActivityDialog } from "./TopicActivity";
import { BotModel } from "./organization-api";

export function texRequest(source: string): string {
  return "Save the following exact TeX source as document.tex using workspace_write, then compile it using tex_compile and publish the resulting PDF attachment. "
    + "Execution must wait for the bot owner's approval in People. Do not compile on the chat server or install dependencies. "
    + "If local tools, bubblewrap, or pdflatex are unavailable, explain what is missing. Return the published attachment Markdown and any important compiler warnings; compilation is not mathematical verification.\n\n"
    + "```tex\n" + source + "\n```";
}

export function TexRequestDialog({ bots, names, onClose, onPrepare }: {
  bots: BotModel[]; names: Record<string, string>; onClose: () => void;
  onPrepare: (source: string, bot: BotModel) => void;
}) {
  const choices = [...new Map(bots.filter(bot => bot.online && bot.local_tools).map(bot => [bot.bot_id, bot])).values()];
  const [selected, setSelected] = useState(choices[0]?.bot_id ?? "");
  const [source, setSource] = useState("\\documentclass{article}\n\\begin{document}\nHello, Reasoning.\n\\end{document}");
  const bot = choices.find(bot => bot.bot_id === selected);
  return <ActivityDialog title="Compile TeX to PDF" onClose={onClose}>
    <p className="org-help">A connected local bot compiles the source after its owner approves execution. Its host needs pdflatex and Linux bubblewrap.
      This prepares a message; nothing runs until you send it. The bot uses its advertised default CLI model.</p>
    {!choices.length && <p role="status">No connected bot advertises local execution. Start an authorized runner with local tools enabled in My bots.</p>}
    <form onSubmit={event => { event.preventDefault(); if (bot && source.trim()) onPrepare(source, bot); }}>
      <label>Local bot<select aria-label="TeX compilation bot" value={selected} onChange={event => setSelected(event.target.value)} disabled={!choices.length}>
        {!choices.length && <option value="">No eligible bot</option>}
        {choices.map(bot => <option key={bot.bot_id} value={bot.bot_id}>{names[bot.bot_id] ?? bot.name}</option>)}</select></label>
      <label>TeX source<textarea aria-label="TeX source" value={source} onChange={event => setSource(event.target.value)} maxLength={14000} rows={12} /></label>
      <button className="org-primary" disabled={!bot || !source.trim()}>Prepare bot message</button>
    </form>
  </ActivityDialog>;
}
