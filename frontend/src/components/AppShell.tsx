import { useState } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import ImportDialog from './ImportDialog';

export default function AppShell() {
  const [importOpen, setImportOpen] = useState(false);

  return (
    <div className="flex min-h-screen bg-white">
      <Sidebar onImportClick={() => setImportOpen(true)} />
      <main className="flex-1 min-w-0">
        <Outlet />
      </main>
      {importOpen && <ImportDialog onClose={() => setImportOpen(false)} />}
    </div>
  );
}
