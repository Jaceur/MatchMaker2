"use client";

// Small shared UI primitives so the pages stay readable and consistent.
import { forwardRef, useState } from "react";

type Variant = "brand" | "success" | "danger" | "ghost" | "outline";

const variantClass: Record<Variant, string> = {
  brand: "bg-brand text-brand-fg hover:opacity-90",
  success: "bg-success text-white hover:opacity-90",
  danger: "bg-danger text-white hover:opacity-90",
  ghost: "bg-transparent text-foreground hover:bg-surface-2",
  outline: "border border-border bg-transparent text-foreground hover:bg-surface-2",
};

export const Button = forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }
>(function Button({ variant = "brand", className = "", ...props }, ref) {
  return (
    <button
      ref={ref}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition
        disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-[var(--ring)]
        ${variantClass[variant]} ${className}`}
      {...props}
    />
  );
});

export function Card({
  className = "",
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`rounded-2xl border border-border bg-surface shadow-sm ${className}`}
    >
      {children}
    </div>
  );
}

export function Progress({ value, label }: { value: number; label?: string }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div>
      {label && (
        <div className="mb-1 flex justify-between text-xs text-muted">
          <span>{label}</span>
          <span>{pct}%</span>
        </div>
      )}
      <div className="h-2 w-full overflow-hidden rounded-full bg-surface-2">
        <div
          className="h-full rounded-full bg-brand transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <div
      className={`h-5 w-5 animate-spin rounded-full border-2 border-border border-t-brand ${className}`}
    />
  );
}

// Copies `text` to the clipboard and flashes confirmation. Caller supplies the
// styling via className so it can be a chip, an outline button, etc.
export function CopyButton({
  text,
  children,
  copiedLabel = "✓ Copied",
  className = "",
}: {
  text: string;
  children: React.ReactNode;
  copiedLabel?: React.ReactNode;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }
  return (
    <button type="button" onClick={copy} className={className} aria-live="polite">
      {copied ? copiedLabel : children}
    </button>
  );
}

export function Chip({
  children,
  tone = "default",
}: {
  children: React.ReactNode;
  tone?: "default" | "brand" | "success" | "danger" | "warning";
}) {
  const tones: Record<string, string> = {
    default: "bg-surface-2 text-muted",
    brand: "bg-brand/10 text-brand",
    success: "bg-success/10 text-success",
    danger: "bg-danger/10 text-danger",
    warning: "bg-warning/10 text-warning",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
