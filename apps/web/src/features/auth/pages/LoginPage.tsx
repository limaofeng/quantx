import {
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  ShieldCheck,
  UserRound,
} from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useLocation } from 'wouter';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { AuthApiError, useAuth } from '@/core/auth';

import { safeInternalPath } from '../safe-internal-path';

function loginErrorMessage(error: unknown): string {
  if (error instanceof AuthApiError) {
    if (error.status === 429) return '登录尝试过于频繁，请稍后再试。';
    if (error.status === 403)
      return '当前页面来源未被授权，请检查 Web Origin 配置。';
    if (error.status === 503) return '服务端认证尚未完成安全配置。';
    return error.message;
  }
  if (error instanceof TypeError)
    return '无法连接认证服务，请检查后端是否运行。';
  return error instanceof Error ? error.message : '登录失败，请稍后重试。';
}

export function LoginPage({ nextPath }: { nextPath: string }) {
  const { login } = useAuth();
  const [, navigate] = useLocation();
  const usernameRef = useRef<HTMLInputElement>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    usernameRef.current?.focus();
  }, []);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError('请输入用户名和密码。');
      return;
    }

    setIsSubmitting(true);
    setError(null);
    try {
      await login({
        username: username.trim(),
        password,
        deviceName: `QuantX Web · ${navigator.platform || 'Browser'}`,
      });
      navigate(safeInternalPath(nextPath), { replace: true });
    } catch (submitError) {
      setError(loginErrorMessage(submitError));
      setPassword('');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#050914] px-4 py-10 text-slate-100">
      <div
        className="pointer-events-none absolute inset-0 opacity-60"
        aria-hidden="true"
      >
        <div className="absolute left-[-12rem] top-[-12rem] h-[28rem] w-[28rem] rounded-full bg-red-600/10 blur-[120px]" />
        <div className="absolute bottom-[-14rem] right-[-10rem] h-[32rem] w-[32rem] rounded-full bg-blue-600/10 blur-[140px]" />
        <div className="absolute inset-0 bg-[linear-gradient(rgba(148,163,184,0.035)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,0.035)_1px,transparent_1px)] bg-[size:48px_48px]" />
      </div>

      <section className="relative grid w-full max-w-5xl overflow-hidden rounded-2xl border border-white/10 bg-[#0a1020]/95 shadow-2xl shadow-black/40 backdrop-blur-xl lg:grid-cols-[1.05fr_0.95fr]">
        <div className="hidden min-h-[620px] flex-col justify-between border-r border-white/10 bg-[#080e1b] p-10 lg:flex">
          <div>
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-red-500/20 bg-red-500/10 text-red-400">
                <ShieldCheck className="h-5 w-5" />
              </div>
              <div>
                <p className="font-mono text-[11px] font-semibold tracking-[0.28em] text-red-400">
                  QUANTX SECURE
                </p>
                <h1 className="mt-1 text-xl font-semibold text-white">
                  本地交易工作台
                </h1>
              </div>
            </div>

            <div className="mt-20 max-w-sm">
              <p className="font-mono text-xs uppercase tracking-[0.24em] text-slate-500">
                Session protection
              </p>
              <h2 className="mt-4 text-3xl font-semibold leading-tight text-slate-50">
                让交易权限停留在受控会话中
              </h2>
              <p className="mt-5 text-sm leading-7 text-slate-400">
                Access Token 仅保存在当前页面内存，长期刷新凭证由浏览器的
                HttpOnly Cookie 隔离保存。
              </p>
            </div>
          </div>

          <div className="space-y-3 text-xs text-slate-400">
            <div className="flex items-center gap-3 rounded-lg border border-white/[0.06] bg-white/[0.025] px-4 py-3">
              <LockKeyhole className="h-4 w-4 text-emerald-400" />
              不在 localStorage 或 sessionStorage 保存 Token
            </div>
            <div className="flex items-center gap-3 rounded-lg border border-white/[0.06] bg-white/[0.025] px-4 py-3">
              <KeyRound className="h-4 w-4 text-blue-400" />
              会话到期前自动安全轮换并重连实时通道
            </div>
          </div>
        </div>

        <div className="flex min-h-[560px] items-center p-6 sm:p-10 lg:p-12">
          <div className="mx-auto w-full max-w-sm">
            <div className="mb-8 lg:hidden">
              <div className="flex items-center gap-3">
                <ShieldCheck className="h-7 w-7 text-red-400" />
                <div>
                  <p className="font-mono text-[10px] tracking-[0.25em] text-red-400">
                    QUANTX SECURE
                  </p>
                  <p className="mt-1 text-sm font-semibold text-white">
                    本地交易工作台
                  </p>
                </div>
              </div>
            </div>

            <div>
              <h2 className="text-2xl font-semibold tracking-tight text-white">
                登录 QuantX
              </h2>
              <p className="mt-2 text-sm text-slate-400">
                使用本机环境中创建的账户继续访问。
              </p>
            </div>

            <form className="mt-8 space-y-5" onSubmit={handleSubmit}>
              <div className="space-y-2">
                <Label htmlFor="username" className="text-sm text-slate-300">
                  用户名
                </Label>
                <div className="relative">
                  <UserRound className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                  <Input
                    ref={usernameRef}
                    id="username"
                    name="username"
                    value={username}
                    onChange={event => setUsername(event.target.value)}
                    autoComplete="username"
                    spellCheck={false}
                    disabled={isSubmitting}
                    className="h-11 border-white/10 bg-black/20 pl-10 text-slate-100 placeholder:text-slate-600 focus-visible:border-red-500/70 focus-visible:ring-red-500/10"
                    placeholder="输入用户名"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="password" className="text-sm text-slate-300">
                  密码
                </Label>
                <div className="relative">
                  <LockKeyhole className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                  <Input
                    id="password"
                    name="password"
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={event => setPassword(event.target.value)}
                    autoComplete="current-password"
                    disabled={isSubmitting}
                    className="h-11 border-white/10 bg-black/20 pl-10 pr-11 text-slate-100 placeholder:text-slate-600 focus-visible:border-red-500/70 focus-visible:ring-red-500/10"
                    placeholder="输入密码"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(value => !value)}
                    className="absolute right-2.5 top-1/2 flex h-8 w-8 -translate-y-1/2 cursor-pointer items-center justify-center rounded-md text-slate-500 transition-colors duration-200 hover:bg-white/5 hover:text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/60"
                    aria-label={showPassword ? '隐藏密码' : '显示密码'}
                  >
                    {showPassword ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </button>
                </div>
              </div>

              {error && (
                <Alert
                  variant="destructive"
                  className="border-red-500/20 bg-red-500/[0.07] text-red-300"
                >
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}

              <Button
                type="submit"
                disabled={isSubmitting}
                className="h-11 w-full cursor-pointer rounded-lg bg-red-600 text-white shadow-lg shadow-red-600/15 hover:bg-red-500 focus-visible:ring-red-500"
              >
                {isSubmitting ? (
                  <>
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                    正在建立安全会话
                  </>
                ) : (
                  <>
                    <ShieldCheck className="h-4 w-4" />
                    安全登录
                  </>
                )}
              </Button>
            </form>

            <p className="mt-8 text-center text-[11px] leading-5 text-slate-600">
              仅在受信任的本地网络与设备上登录。系统不会在前端持久化访问令牌。
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}
