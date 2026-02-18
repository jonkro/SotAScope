import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import SearchInput from '../components/SearchInput';
import EmptyState from '../components/EmptyState';
import ProjectFormDialog from '../components/ProjectFormDialog';
import ConfirmDialog from '../components/ConfirmDialog';
import { useProjects, useCreateProject, useDeleteProject } from '../hooks/useProjects';

export default function ProjectsPage() {
  const [search, setSearch] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [deleteId, setDeleteId] = useState<number | null>(null);
  const navigate = useNavigate();

  const { data: projects, isLoading } = useProjects({ q: search || undefined });
  const createMut = useCreateProject();
  const deleteMut = useDeleteProject();

  const handleSearch = useCallback((v: string) => setSearch(v), []);

  return (
    <div className="flex flex-col h-screen">
      <PageHeader title="Projects">
        <SearchInput value={search} onChange={handleSearch} placeholder="Search projects..." />
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
        >
          New Project
        </button>
      </PageHeader>

      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <p className="text-sm text-gray-400">Loading...</p>
        ) : !projects?.length ? (
          <EmptyState message="No projects yet.">
            <button
              onClick={() => setShowCreate(true)}
              className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
            >
              Create your first project
            </button>
          </EmptyState>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <div
                key={p.id}
                onClick={() => navigate(`/projects/${p.id}`)}
                className="border border-gray-200 rounded-lg p-4 hover:border-blue-300 hover:shadow-sm cursor-pointer transition-all"
              >
                <div className="flex items-start justify-between">
                  <h3 className="font-medium text-gray-900">{p.name}</h3>
                  <button
                    onClick={(e) => { e.stopPropagation(); setDeleteId(p.id); }}
                    className="text-xs text-red-400 hover:text-red-600"
                  >
                    Delete
                  </button>
                </div>
                {p.description && <p className="text-sm text-gray-500 mt-1 line-clamp-2">{p.description}</p>}
                <div className="text-xs text-gray-400 mt-2">
                  {p.owner && <span>Owner: {p.owner}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showCreate && (
        <ProjectFormDialog
          onCancel={() => setShowCreate(false)}
          onSubmit={(data) => {
            createMut.mutate(data, { onSuccess: () => setShowCreate(false) });
          }}
        />
      )}

      {deleteId !== null && (
        <ConfirmDialog
          title="Delete project"
          message="This will permanently delete the project and all its topic lists."
          onCancel={() => setDeleteId(null)}
          onConfirm={() => {
            deleteMut.mutate(deleteId, { onSuccess: () => setDeleteId(null) });
          }}
        />
      )}
    </div>
  );
}
