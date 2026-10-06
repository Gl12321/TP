# Показатели и условия сравнения

[ScopeFilters](ScopeFilters.tsx) хранит период, точки и показатель в URL. [MetricCard](MetricCard.tsx) отображает отдельное значение, [Attainment](Attainment.tsx) — выполнение плана, а [PeriodComparison](PeriodComparison.tsx) — сравнение периодов, переданное сервером.

Компоненты используются в [обзоре](../pages/OverviewPage.tsx) и [странице точки](../../stores/pages/StoresPage.tsx). Для одной точки `ScopeFilters` получает `fixedStore`, чтобы не предлагать смену области. Карточки и полосы представляют готовые значения и не выполняют отдельный расчёт показателя.
