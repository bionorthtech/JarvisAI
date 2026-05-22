/**
 * B2 — Greeting popup.
 *
 * Modal opened via the global `jarvis://greet` Tauri hotkey.
 * One-shot input that hands the entered text back to App via
 * onSubmit (which switches to chat mode + dispatches it).
 */
import { useEffect, useRef, useState } from "react";
import { Bot, X } from "lucide-react";
import { motion } from "framer-motion";
import { useTheme } from "../hooks/useTheme";
import { easeStandard, durBase } from "../utils/motion";

export default function GreetingPopup({ onSubmit, onClose }: { onSubmit: (text: string) => void; onClose: () => void }) {
  const { t } = useTheme();
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { inputRef.current?.focus(); }, []);
  const go = () => { if (value.trim()) { onSubmit(value.trim()); onClose(); } };
  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
    >
      <motion.div
        className="bg-[var(--surface-1)] rounded-2xl apple-card-shadow p-6 w-[520px] max-w-[90vw]"
        initial={{ opacity: 0, scale: 0.95, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97 }}
        transition={{ duration: durBase, ease: easeStandard }}
      >
        <div className="flex items-center gap-3 mb-4">
          <Bot className={`w-5 h-5 ${t.accent}`} />
          <span className="text-base font-semibold text-[var(--text)]">JARVIS online — what do you need?</span>
          <button onClick={onClose} className="ml-auto text-[var(--text-subtle)] hover:text-[var(--text)]"><X className="w-4 h-4" /></button>
        </div>
        <input ref={inputRef} value={value} onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === "Escape") onClose(); if (e.key === "Enter") go(); }}
          placeholder="Ask JARVIS anything…"
          className={`w-full bg-[var(--surface-2)] rounded-xl px-4 py-3 text-sm text-[var(--text)] placeholder:text-[var(--text-subtle)] focus:outline-none ${t.inputFocus}`} />
        <p className="mt-3 text-[11px] text-[var(--text-subtle)]">Enter to send · Esc to dismiss</p>
      </motion.div>
    </motion.div>
  );
}
