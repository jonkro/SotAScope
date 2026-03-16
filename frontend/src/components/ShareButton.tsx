import { useState } from 'react';

function fallbackCopyText(text: string): boolean {
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    // execCommand not supported
  }
  document.body.removeChild(textarea);
  return ok;
}

export function ShareButton({ href }: { href?: string }) {
  const [status, setStatus] = useState<'idle' | 'copied' | 'failed'>('idle');

  const handleShare = () => {
    const url = href ?? window.location.href;
    navigator.clipboard.writeText(url).then(() => {
      setStatus('copied');
      setTimeout(() => setStatus('idle'), 1000);
    }).catch(() => {
      const ok = fallbackCopyText(url);
      setStatus(ok ? 'copied' : 'failed');
      setTimeout(() => setStatus('idle'), ok ? 1000 : 4000);
    });
  };

  const overlayLabel =
    status === 'copied' ? 'Copied to clipboard' :
    status === 'failed' ? 'Copy failed — use HTTPS' :
    null;

  return (
    <div className="relative">
      <button
        onClick={handleShare}
        className="h-8 w-8 flex items-center justify-center border border-gray-300 rounded hover:bg-gray-50 text-gray-500"
        title="Copy link"
      >
        {status === 'copied' ? (
          /* Checkmark icon */
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        ) : (
          /* Chain/link icon */
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
          </svg>
        )}
      </button>

      {overlayLabel && (
        <span className="absolute right-0 top-full mt-1.5 whitespace-nowrap text-xs bg-gray-800 text-white px-2 py-1 rounded pointer-events-none z-50">
          {overlayLabel}
        </span>
      )}
    </div>
  );
}
