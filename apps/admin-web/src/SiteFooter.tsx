export function SiteFooter({ compact = false }: { compact?: boolean }) {
  return (
    <footer className={compact ? "site-footer site-footer-compact" : "site-footer"}>
      <span>篮球赛事管理系统</span>
      <a href="https://beian.miit.gov.cn/" rel="noreferrer" target="_blank">
        京ICP备2024054219号-2
      </a>
    </footer>
  );
}
