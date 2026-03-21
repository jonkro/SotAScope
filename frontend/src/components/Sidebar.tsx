import { useRef, useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { OnboardingHintSequence } from './OnboardingHint';

const links = [
  { to: '/projects', label: 'Projects' },
  { to: '/library', label: 'Library' },
  { to: '/venues', label: 'Venues' },
  { to: '/settings', label: 'Settings' },
];

export default function Sidebar() {
  const location = useLocation();
  // Remember the last projects path so clicking "Projects" from another section returns there
  const lastProjectsPath = useRef('/projects');

  if (location.pathname.startsWith('/projects')) {
    lastProjectsPath.current = location.pathname;
  }

  // Refs for app-level onboarding hints
  const newProjectBtnRef = useRef<HTMLElement | null>(null);
  const venuesLinkRef = useRef<HTMLAnchorElement | null>(null);
  const settingsLinkRef = useRef<HTMLAnchorElement | null>(null);

  // Query the "New Project" button rendered by ProjectsPage (in the Outlet)
  useEffect(() => {
    newProjectBtnRef.current = document.querySelector<HTMLElement>('[data-hint="new-project"]');
  });

  return (
    <aside className="w-56 shrink-0 border-r border-gray-200 bg-gray-50 flex flex-col h-screen sticky top-0">
      <div className="px-4 py-5 font-semibold text-lg text-gray-800">LitExplorer</div>
      <nav className="flex-1 px-2 space-y-1">
        {links.map((l) => {
          const href = l.to === '/projects' ? lastProjectsPath.current : l.to;
          const isActive = location.pathname.startsWith(l.to);
          return (
            <Link
              key={l.to}
              to={href}
              ref={
                l.to === '/venues'
                  ? venuesLinkRef
                  : l.to === '/settings'
                    ? settingsLinkRef
                    : undefined
              }
              className={`block px-3 py-2 rounded text-sm font-medium ${
                isActive ? 'bg-blue-100 text-blue-800' : 'text-gray-700 hover:bg-gray-100'
              }`}
            >
              {l.label}
            </Link>
          );
        })}
      </nav>

      <OnboardingHintSequence
        hints={[
          {
            anchorRef: newProjectBtnRef,
            storageKey: 'litexplorer:onboarding:app:new-project',
            text: 'Create or import a project to get started.',
            placement: 'bottom',
          },
          {
            anchorRef: venuesLinkRef,
            storageKey: 'litexplorer:onboarding:app:venues',
            text: 'Manage venues across projects.',
            placement: 'right',
          },
          {
            anchorRef: settingsLinkRef,
            storageKey: 'litexplorer:onboarding:app:settings',
            text: 'Check settings for full performance.',
            placement: 'right',
          },
        ]}
      />
    </aside>
  );
}
