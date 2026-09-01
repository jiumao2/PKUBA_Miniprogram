export function SiteFooter({ compact = false }: { compact?: boolean }) {
  return (
    <footer className={compact ? "site-footer site-footer-compact" : "site-footer"}>
      <span>篮球赛事管理系统</span>
      <a href="https://beian.miit.gov.cn/" rel="noreferrer" target="_blank">
        京ICP备2024054219号-2
      </a>
      <a
        className="site-footer-police"
        href="https://beian.mps.gov.cn/#/query/webSearch?code=11010802050065"
        rel="noreferrer"
        target="_blank"
      >
        <img aria-hidden="true" alt="" height="40" src="/beian-police.png" width="36" />
        <span>京公网安备11010802050065号</span>
      </a>
    </footer>
  );
}
