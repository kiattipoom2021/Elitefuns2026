/* Shared sidebar — one source of truth for nav across all pages.
 *
 * Usage on any page:
 *   <aside class="w-60 border-r border-border-subtle bg-bg-elevated flex flex-col flex-shrink-0"
 *          id="sidebar-mount"></aside>
 *   <script src="/design/sidebar.js"></script>
 *
 * Emits standard IDs (#avatar, #user-email, #logout-btn, #nav-admin-link)
 * เพื่อ backward-compat กับ inline script ที่ page มีอยู่แล้ว.
 * Active nav = auto-detect ตาม location.pathname.prefix.
 */
(() => {
  'use strict';

  const NAV_ITEMS = [
    {
      href: '/dashboard/',
      label: 'Dashboard',
      icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/>',
    },
    {
      href: '/connect-mt5/',
      label: 'พอร์ต MT5',
      icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/><path stroke-linecap="round" stroke-linejoin="round" d="M12 3v9l6 3"/>',
    },
    {
      href: '/optimize/',
      label: 'Optimize',
      icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M3 6h13M3 12h9m-9 6h6M17 14l4 4m0 0l-4 4m4-4H13"/>',
    },
    {
      href: '/bot-trade/',
      label: 'Bot Trade',
      icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"/>',
    },
    {
      href: '/arbitrage/',
      label: 'Arbitrage',
      icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4"/>',
    },
    {
      href: '/console-data/',
      label: 'Console Data',
      icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M4 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1V5zm10 0a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1V5zM4 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1v-4zm10 0a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z"/>',
    },
    {
      href: '/admin/',
      label: 'Admin',
      id: 'nav-admin-link',
      adminOnly: true,
      icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/>',
    },
  ];

  function activeCls(href) {
    // match ทั้ง /dashboard/ และ /dashboard/index.html
    // (แต่ / อย่างเดียวไม่นับเป็น active ของ /console-data/)
    const p = location.pathname;
    if (p === href) return 'active';
    // นับ prefix match — เช่น /port/?id=x กด Dashboard active ไม่ได้
    // แต่ /console-data/anything → active console-data
    if (href !== '/' && p.startsWith(href)) return 'active';
    return '';
  }

  function iconSvg(inner) {
    return `<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">${inner}</svg>`;
  }

  function render(mount) {
    const links = NAV_ITEMS.map(it => `
      <a ${it.id ? `id="${it.id}"` : ''} href="${it.href}" class="nav-link ${activeCls(it.href)} ${it.adminOnly ? 'admin-only' : ''}">
        ${iconSvg(it.icon)}
        ${it.label}
      </a>`).join('');

    mount.innerHTML = `
      <div class="px-5 py-5 border-b border-border-subtle">
        <a href="/dashboard/" class="brand-mark">
          <img src="/design/logo.png" alt="ELITEFUNS">
          <span class="word">Elitefuns</span>
        </a>
      </div>
      <nav class="flex-1 px-3 py-4 space-y-0.5">
        <div class="px-2 mb-1.5 text-[10px] uppercase tracking-wider font-medium text-fg-subtle">เมนูหลัก</div>
        ${links}
      </nav>
      <div class="p-3 border-t border-border-subtle">
        <div class="flex items-center gap-2.5 px-2 py-2">
          <div id="avatar" class="w-8 h-8 rounded-full bg-info flex items-center justify-center font-semibold text-xs flex-shrink-0" style="color: var(--cta-text);">?</div>
          <div class="flex-1 min-w-0">
            <div id="user-email" class="text-fg text-xs font-medium truncate">กำลังโหลด...</div>
            <div class="text-fg-subtle text-[10px]">Free plan</div>
          </div>
          <button id="logout-btn" title="ออกจากระบบ"
            class="text-fg-muted hover:text-danger transition-colors p-1 rounded">
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/>
            </svg>
          </button>
        </div>
      </div>
    `;
  }

  const mount = document.getElementById('sidebar-mount');
  if (mount) render(mount);
})();
