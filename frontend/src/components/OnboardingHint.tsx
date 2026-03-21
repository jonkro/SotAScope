import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

const TOOLTIP_WIDTH = 240;
const GAP = 8;
const ARROW_SIZE = 7;

interface OnboardingHintProps {
  anchorRef: { current: HTMLElement | null };
  text: string;
  storageKey: string;
  placement?: 'top' | 'bottom' | 'left' | 'right';
  onDismiss?: () => void;
}

interface TooltipPos {
  top: number;
  left: number;
  arrowLeft: number;
}

function computePos(el: HTMLElement, placement: string): TooltipPos {
  const rect = el.getBoundingClientRect();
  const centerX = rect.left + rect.width / 2;
  const rawLeft = centerX - TOOLTIP_WIDTH / 2;
  const clampedLeft = Math.min(
    window.innerWidth - TOOLTIP_WIDTH - 8,
    Math.max(8, rawLeft),
  );
  const arrowLeft = centerX - clampedLeft;
  const top =
    placement === 'top'
      ? rect.top - GAP - ARROW_SIZE - 72 // 72 ≈ tooltip height estimate
      : rect.bottom + GAP;
  return { top, left: clampedLeft, arrowLeft };
}

export function OnboardingHint({
  anchorRef,
  text,
  storageKey,
  placement = 'bottom',
  onDismiss,
}: OnboardingHintProps) {
  const [dismissed, setDismissed] = useState(() => !!localStorage.getItem(storageKey));
  const [pos, setPos] = useState<TooltipPos | null>(null);

  useEffect(() => {
    if (dismissed) return;
    function tryCompute() {
      const el = anchorRef.current;
      if (!el) return false;
      setPos(computePos(el, placement));
      return true;
    }
    if (!tryCompute()) {
      const id = window.setTimeout(tryCompute, 80);
      return () => clearTimeout(id);
    }
  }, [dismissed, anchorRef, placement]);

  if (dismissed || !pos) return null;

  const handleDismiss = () => {
    localStorage.setItem(storageKey, 'true');
    setDismissed(true);
    onDismiss?.();
  };

  const isAbove = placement === 'top';

  return createPortal(
    <>
      {/* Subtle backdrop — click anywhere to dismiss */}
      <div
        className="fixed inset-0 z-[9998]"
        style={{ background: 'rgba(0,0,0,0.08)' }}
        onClick={handleDismiss}
      />
      {/* Tooltip */}
      <div
        className="fixed z-[9999] bg-white rounded-lg shadow-lg border border-gray-200 p-3"
        style={{ width: TOOLTIP_WIDTH, top: pos.top, left: pos.left }}
      >
        {/* Arrow outer (border color) */}
        <div
          style={{
            position: 'absolute',
            ...(isAbove
              ? { bottom: -(ARROW_SIZE + 1) }
              : { top: -(ARROW_SIZE + 1) }),
            left: pos.arrowLeft,
            transform: 'translateX(-50%)',
            width: 0,
            height: 0,
            ...(isAbove
              ? {
                  borderLeft: `${ARROW_SIZE + 1}px solid transparent`,
                  borderRight: `${ARROW_SIZE + 1}px solid transparent`,
                  borderTop: `${ARROW_SIZE + 1}px solid #e5e7eb`,
                }
              : {
                  borderLeft: `${ARROW_SIZE + 1}px solid transparent`,
                  borderRight: `${ARROW_SIZE + 1}px solid transparent`,
                  borderBottom: `${ARROW_SIZE + 1}px solid #e5e7eb`,
                }),
          }}
        />
        {/* Arrow inner (white fill) */}
        <div
          style={{
            position: 'absolute',
            ...(isAbove ? { bottom: -ARROW_SIZE } : { top: -ARROW_SIZE }),
            left: pos.arrowLeft,
            transform: 'translateX(-50%)',
            width: 0,
            height: 0,
            ...(isAbove
              ? {
                  borderLeft: `${ARROW_SIZE}px solid transparent`,
                  borderRight: `${ARROW_SIZE}px solid transparent`,
                  borderTop: `${ARROW_SIZE}px solid white`,
                }
              : {
                  borderLeft: `${ARROW_SIZE}px solid transparent`,
                  borderRight: `${ARROW_SIZE}px solid transparent`,
                  borderBottom: `${ARROW_SIZE}px solid white`,
                }),
          }}
        />
        <p className="text-xs text-gray-700 leading-relaxed mb-2">{text}</p>
        <button
          onClick={handleDismiss}
          className="text-xs text-blue-600 hover:text-blue-800 font-medium"
        >
          Got it
        </button>
      </div>
    </>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// Sequence wrapper — shows hints one at a time
// ---------------------------------------------------------------------------

interface SequenceHint {
  anchorRef: { current: HTMLElement | null };
  text: string;
  storageKey: string;
  placement?: 'top' | 'bottom' | 'left' | 'right';
  /** Called after the hint is dismissed (after storageKey is written). */
  onDismiss?: () => void;
}

export function OnboardingHintSequence({ hints }: { hints: SequenceHint[] }) {
  const [currentIdx, setCurrentIdx] = useState<number>(() =>
    hints.findIndex((h) => !localStorage.getItem(h.storageKey)),
  );

  // Stable reference to the current hint for the effect below
  const currentHintRef = useRef<SequenceHint | null>(null);
  currentHintRef.current =
    currentIdx >= 0 && currentIdx < hints.length ? hints[currentIdx] : null;

  // Auto-skip to the next hint if the current anchor element is not mounted.
  // Waits 150 ms for the DOM to settle before skipping.
  useEffect(() => {
    const hint = currentHintRef.current;
    if (!hint || hint.anchorRef.current) return;
    const id = window.setTimeout(() => {
      if (!currentHintRef.current?.anchorRef.current) {
        setCurrentIdx((prev) => {
          const next = hints.findIndex(
            (h, i) => i > prev && !localStorage.getItem(h.storageKey),
          );
          return next === -1 ? hints.length : next;
        });
      }
    }, 150);
    return () => clearTimeout(id);
  // Re-run when currentIdx changes so a newly-skipped hint triggers another check
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentIdx]);

  if (currentIdx < 0 || currentIdx >= hints.length) return null;

  const hint = hints[currentIdx];

  const handleDismiss = () => {
    hint.onDismiss?.();
    const next = hints.findIndex(
      (h, i) => i > currentIdx && !localStorage.getItem(h.storageKey),
    );
    setCurrentIdx(next === -1 ? hints.length : next);
  };

  return (
    <OnboardingHint
      key={hint.storageKey}
      anchorRef={hint.anchorRef}
      text={hint.text}
      storageKey={hint.storageKey}
      placement={hint.placement}
      onDismiss={handleDismiss}
    />
  );
}
