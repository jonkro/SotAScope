import type { ReactNode } from 'react';

export default function PageHeader({
  title,
  leftContent,
  children,
}: {
  title?: string;
  leftContent?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
      {leftContent ?? <h1 className="text-xl font-semibold text-gray-900">{title}</h1>}
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}
