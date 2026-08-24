/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 生产构建时注入的后端 API 基址，如 https://api.example.com/api/v1 */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
