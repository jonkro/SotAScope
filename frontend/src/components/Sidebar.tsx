import { Link, useLocation } from 'react-router-dom';

const links = [
  { to: '/projects', label: 'Projects' },
  { to: '/library', label: 'Library' },
  { to: '/venues', label: 'Venues' },
  { to: '/settings', label: 'Settings' },
];

function getProjectsLink(): string {
  try {
    const id = localStorage.getItem('litexplorer:lastProjectId');
    if (id) return `/projects/${id}`;
  } catch { /* ignore */ }
  return '/projects';
}

export default function Sidebar() {
  const location = useLocation();

  return (
    <aside className="w-56 shrink-0 border-r border-gray-200 bg-gray-50 flex flex-col h-screen sticky top-0">
      <div className="px-4 py-5 font-semibold text-lg text-gray-800">LitExplorer</div>
      <nav className="flex-1 px-2 space-y-1">
        {links.map((l) => {
          const href = l.to === '/projects' ? getProjectsLink() : l.to;
          const isActive = location.pathname.startsWith(l.to);
          return (
            <Link
              key={l.to}
              to={href}
              className={`block px-3 py-2 rounded text-sm font-medium ${
                isActive ? 'bg-blue-100 text-blue-800' : 'text-gray-700 hover:bg-gray-100'
              }`}
            >
              {l.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
