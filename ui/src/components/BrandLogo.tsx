import { cn } from "@/lib/utils";

// Text-first local brand lockup so the demo does not depend on a celebrity
// portrait, cloned likeness, or third-party image asset.
export function BrandLogo({
  className,
  inverse = false,
  mark = false,
}: {
  className?: string;
  inverse?: boolean;
  mark?: boolean;
}) {
  if (mark) {
    return (
      <span
        aria-label="易水 AI 顾问"
        className={cn(
          "inline-flex aspect-square h-7 select-none items-center justify-center rounded-full border border-amber-400/40 bg-amber-400/10 text-sm font-semibold text-amber-700 dark:text-amber-200",
          className,
        )}
      >
        易
      </span>
    );
  }

  return (
    <span
      aria-label="易水 AI 顾问"
      className={cn(
        "inline-flex h-7 select-none items-center gap-2 whitespace-nowrap text-foreground",
        inverse && "text-amber-50",
        className,
      )}
    >
      <span className="inline-flex aspect-square h-full items-center justify-center rounded-full border border-current/30 bg-amber-400/10 text-sm font-semibold text-amber-700 dark:text-amber-200">
        易
      </span>
      <span className="flex items-baseline gap-1 leading-none">
        <span className="text-base font-semibold tracking-[0.14em]">易水 AI</span>
        <span className="text-[10px] tracking-[0.16em] opacity-60">语音顾问</span>
      </span>
    </span>
  );
}
