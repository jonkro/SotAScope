import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchWorkPDFs, uploadWorkPDF, setWorkPDFPrimary, deleteWorkPDF, extractWorkPDFText } from '../api';

export function useWorkPDFs(workId: number | null) {
  return useQuery({
    queryKey: ['works', workId, 'pdfs'],
    queryFn: () => fetchWorkPDFs(workId!),
    enabled: workId != null,
  });
}

export function useUploadWorkPDF() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, file }: { workId: number; file: File }) => uploadWorkPDF(workId, file),
    onSuccess: (_data, { workId }) => {
      qc.invalidateQueries({ queryKey: ['works', workId, 'pdfs'] });
    },
  });
}

export function useSetWorkPDFPrimary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, pdfId }: { workId: number; pdfId: number }) =>
      setWorkPDFPrimary(workId, pdfId),
    onSuccess: (_data, { workId }) => {
      qc.invalidateQueries({ queryKey: ['works', workId, 'pdfs'] });
    },
  });
}

export function useDeleteWorkPDF() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, pdfId }: { workId: number; pdfId: number }) =>
      deleteWorkPDF(workId, pdfId),
    onSuccess: (_data, { workId }) => {
      qc.invalidateQueries({ queryKey: ['works', workId, 'pdfs'] });
    },
  });
}

export function useExtractWorkPDFText() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, pdfId }: { workId: number; pdfId: number }) =>
      extractWorkPDFText(workId, pdfId),
    onSettled: (_data, _err, { workId }) => {
      qc.invalidateQueries({ queryKey: ['works', workId, 'pdfs'] });
    },
  });
}
