import { create } from 'zustand';

interface ToastState {
  message: string | null;
  kind: 'info' | 'error' | 'success';
  show: (message: string, kind?: ToastState['kind']) => void;
  hide: () => void;
}

let timer: ReturnType<typeof setTimeout> | null = null;

export const useToast = create<ToastState>((set) => ({
  message: null,
  kind: 'info',
  show: (message, kind = 'info') => {
    if (timer) clearTimeout(timer);
    set({ message, kind });
    timer = setTimeout(() => set({ message: null }), 3000);
  },
  hide: () => set({ message: null }),
}));

export const toast = (message: string, kind: ToastState['kind'] = 'info') => useToast.getState().show(message, kind);
