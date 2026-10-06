# Управление командой и пространством

[MembersPanel](MembersPanel.tsx) и [MemberDialog](MemberDialog.tsx) показывают участников и меняют их роль, область точек и доступ. [InvitationsPanel](InvitationsPanel.tsx) и [InvitationDialog](InvitationDialog.tsx) создают одноразовое приглашение и показывают выданную ссылку.

[StoreDirectory](StoreDirectory.tsx) и [StoreDialog](StoreDialog.tsx) ведут названия и внешние коды точек. [SecurityPanel](SecurityPanel.tsx) меняет пароль и завершает другие сеансы, а [CreateWorkspace](CreateWorkspace.tsx) создаёт независимое пространство.

Панели используются на [странице настроек](../pages/SettingsPage.tsx) в контексте текущей сессии и пространства. После изменения доступа сессия обновляется; подписи полномочий берутся из [описаний ролей](../model/roles.ts), а фактические ограничения проверяет API.
