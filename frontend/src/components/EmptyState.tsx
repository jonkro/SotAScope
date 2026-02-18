import type { ReactNode } from 'react';

export default function EmptyState({ message, children }: { message: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <p className="text-gray-500 mb-4">{message}</p>
      {children}
    </div>
  );
}
