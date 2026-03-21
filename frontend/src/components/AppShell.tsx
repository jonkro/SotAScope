import { useState, useEffect, useRef } from 'react';
import { Outlet, Link } from 'react-router-dom';
import Sidebar from './Sidebar';
import { useSettings } from '../hooks/useSettings';

const EMAIL_DISMISS_KEY = 'sotascope:emailWarningDismissed';

function EmailWarningBanner() {
  const { data: settings } = useSettings();
  const [dismissed, setDismissed] = useState(false);
  const prevEmailRef = useRef<string | undefined>(undefined);

  const email = settings?.find((s) => s.key === 'api_contact_email')?.value?.trim();

  // Reset dismissed when email transitions from set → unset, so the banner
  // reappears if the user clears their email after previously dismissing.
  useEffect(() => {
    if (settings === undefined) return;
    const prev = prevEmailRef.current;
    prevEmailRef.current = email;
    if (prev && !email) {
      setDismissed(false);
    }
  });

  // Restore dismiss state from localStorage on mount
  useEffect(() => {
    if (localStorage.getItem(EMAIL_DISMISS_KEY) === 'true') {
      setDismissed(true);
    }
  }, []);

  if (!settings) return null;   // loading
  if (email) return null;       // email is configured
  if (dismissed) return null;   // user dismissed this session

  const handleDismiss = () => {
    localStorage.setItem(EMAIL_DISMISS_KEY, 'true');
    setDismissed(true);
  };

  return (
    <div className="fixed top-0 left-56 right-0 z-30 px-4 py-2 bg-amber-50 border-b border-amber-200 flex items-center gap-2 text-xs text-amber-800 shadow-sm">
      <span className="shrink-0">⚠</span>
      <span>
        Please configure your email in{' '}
        <Link to="/settings" className="underline font-medium hover:text-amber-900">
          Settings
        </Link>{' '}
        to ensure reliable data fetching.
      </span>
      <button
        onClick={handleDismiss}
        className="ml-auto shrink-0 text-amber-600 hover:text-amber-900 leading-none px-1"
        aria-label="Dismiss"
      >
        ✕
      </button>
    </div>
  );
}

export default function AppShell() {
  return (
    <div className="flex min-h-screen bg-white">
      <Sidebar />
      <main className="flex-1 min-w-0">
        <EmailWarningBanner />
        <Outlet />
      </main>
    </div>
  );
}
