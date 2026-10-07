# 在庫管理アプリ (Flask) — sqlite3 版

> **この版は、標準の `sqlite3` だけで動く軽量版です。** 機能・画面・挙動は、SQLAlchemy + Flask-Migrate を使う
> 「SQLAlchemy 版」(`inventory_app_sqlalchemy`)と同じになるよう揃えてあります(画面のテンプレートは同一ファイルです)。
> PostgreSQL など SQLite 以外のDBを使いたいときは、SQLAlchemy 版を使ってください。
> 詳しくは「2つの版の関係」を参照してください。

Tシャツ・キャップ・カバン・バッジ等の「無地在庫」と、シルクスクリーン「プリント済み製品」の在庫管理。
Tailscale VPN 内の端末(iPad / iPhone / Windows / Android)からブラウザで使います。

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export FLASK_APP=run.py            # Windows(PowerShell): $env:FLASK_APP="run.py"
flask init-db                      # DB(instance/inventory.db)を作成・更新
flask seed                         # 初期カテゴリー(Tシャツ/キャップ/カバン/バッジ)
flask create-user 管理者名 --admin  # 最初の管理者(パスワードを聞かれます)
flask list-users                   # 登録済みユーザーの確認(パスワードは表示されません)
```

アプリを更新したら `flask init-db` をもう一度実行してください(何度実行しても安全です)。
実行を忘れても、画面には「データベースの更新が必要です」(503)が出るだけで、実行すれば再起動なしで直ります。
未実行のままアクセスすると、「データベースの更新が必要です」(503)と表示されます。
実行すれば、再起動しなくても使えるようになります。

## 起動

開発(自分のPCだけ):

```bash
python run.py        # http://127.0.0.1:5000
```

本番(Tailscale経由で他の端末から使う):

```bash
# Linux / Mac
gunicorn -b 127.0.0.1:8000 -w 1 --threads 4 "app:create_app()"
# Windows
waitress-serve --listen=127.0.0.1:8000 --call app:create_app
```

### Tailscale での公開(推奨)

アプリは `127.0.0.1` だけで待ち受けさせ、`tailscale serve` で tailnet 内にだけ HTTPS 公開します。

```bash
tailscale serve --bg 8000
# → https://<マシン名>.<tailnet名>.ts.net/ で各端末からアクセス
```

HTTPS 経由になるので、次の環境変数を設定してください(Cookie に Secure 属性が付きます)。

```bash
export INVENTORY_COOKIE_SECURE=1
export INVENTORY_BEHIND_PROXY=1
```

- 各端末にも Tailscale アプリを入れ、Tailscale を ON にしておく必要があります。
- 在庫アプリに入れる端末・ユーザーを絞るには Tailscale の ACL を使います。
- `tailscale funnel` は**使わないでください**(インターネットに公開されます)。

## 使い方の流れ

1. 管理者でログイン → 「品番」でブランド・品番を登録(**ブランドか品番のどちらかは必須**、商品名は任意。
   カテゴリーは「カテゴリー」で追加可能)
2. 「入荷」で 品番・色・サイズ(または種類名)・数量 を登録
3. 「プリント変換」で 無地 → プリント済み(使用数と完成数は別々に入力可)
4. 「出荷・調整」で出荷、棚卸しの差分調整(理由必須)
5. 「履歴」で いつ・誰が・何を変えたか を確認
6. 「備考」で、品番ごと(または色ごと)の自由なメモを残す(下記)

## 備考(メモ)

無地在庫の画面で、品番のカードにある「備考を追加」から、誰でも書けます(任意入力です)。

- 例: 「これで入荷は最後。この色は、別ブランドの同じ色か近い色を、仕入れ先を変えて探す」
- 「対象の色・種類」を入れると、その色の行に「備考」の印が付きます。空欄なら品番全体へのメモです。
- 未対応の備考は、ダッシュボード・入荷画面・「備考」メニューに出ます。片付いたら「対応済みにする」(戻すことも可)。
- 削除できるのは管理者だけです。書いた人と日時が記録されます。

## カテゴリーの設定

| 設定 | 意味 |
|---|---|
| 色 / サイズ / 種類名 を使う | 入荷画面にその入力欄が出る(例: バッジは種類名だけ) |
| プリント対象 | OFF だとプリント変換の候補に出ない(バッジ等) |

## 設計メモ

- DBは標準ライブラリの `sqlite3` だけを使っています(SQLAlchemy / Alembic は使いません。メモリ節約のため)。
  読み取りクエリは `app/repo.py`、在庫操作は `app/services/stock.py`、スキーマは `app/schema.py` にあります。
- 書き込みは必ず `with transaction():`(`BEGIN IMMEDIATE` ～ `COMMIT`)で包みます。例外が出たら全部ロールバックされ、
  同時に操作が重なっても書き込みは1件ずつ順番に処理されます(在庫の取り合いや二重登録は起きません)。
- 在庫の増減は必ず `app/services/stock.py` の関数を通り、`StockMovement` に履歴が残ります。
  マイナス在庫になる操作は DB 側の更新条件で弾かれ、変換は1トランザクションで両方更新されます。
- 色・サイズ・種類名の未使用列は NULL ではなく空文字で保存します(ユニーク制約を効かせるため)。
- 時刻は UTC で保存し、画面では日本時間(JST)で表示します。
- ログインの有効性には、パスワードから作ったトークンを含めています。管理者がパスワードを再設定すると、
  その人の全端末のログインが切れます(紛失した端末への対策)。本人が自分で変更した場合は、その端末だけ入ったままです。
- 停止中の品番には入荷・プリント変換ができません(残り在庫の出荷・調整はできます)。
- 使用中(在庫のある)の「色・サイズ・種類名」の設定は、カテゴリー編集でOFFにできません。

## SQLAlchemy 版で作ったDBについて

SQLAlchemy 版で作った `inventory.db` も、`flask init-db` で引き継げます(SQLAlchemy 版が実際に作ったDDLでテスト済みです)。
以前の品番の「メモ」欄に書かれていた内容は、備考に移されます。
引き継ぎ前に、必ず `inventory.db` をコピーして保管してください。

## 2つの版の関係

| | sqlite3 版(この版) | SQLAlchemy 版 |
|---|---|---|
| DBアクセス | 標準の `sqlite3` | SQLAlchemy + Flask-Migrate |
| DB作成・更新 | `flask init-db` | `flask db upgrade` |
| 使えるDB | SQLite のみ | SQLite / PostgreSQL ほか |
| メモリ(gunicorn 1ワーカー) | 約37MB | 約71MB |
| 画面・機能・挙動 | 同じ(テンプレート・CSS・JSは同一ファイル) | 同じ |

**DBファイルは、どちらの版でも使えます**(同じテーブル構造です)。

- **SQLAlchemy 版 → この版へ**: `flask init-db` を実行するだけで引き継がれます(SQLAlchemy 版が進めたリビジョンも判別します)。
- **この版 → SQLAlchemy 版へ**: SQLAlchemy 版で、一度だけ `flask db stamp head` を実行してから、`flask db upgrade` します。

どちらに切り替える場合も、先に `inventory.db` をコピーして保管してください。

## DBスキーマを変えるとき

`app/schema.py` の `MIGRATIONS` の**末尾に**SQLを1つ足して、`flask init-db` を実行します。
適用済みの番号は SQLite の `PRAGMA user_version` に保存されるので、何度実行しても安全です。
(SQLAlchemy 版で作ったDBも、構造が同じなのでそのまま引き継がれます。)

## テスト

```bash
pip install -r requirements-dev.txt
pytest
```

## バックアップ

SQLite の `instance/inventory.db` を定期的に別の場所へコピーしてください。
稼働中は `sqlite3 instance/inventory.db ".backup backup.db"` が安全です(WAL モードのため、ファイルを単純コピーするだけだと最新分が欠けることがあります)。

## 設定(環境変数)

| 変数 | 内容 |
|---|---|
| `INVENTORY_SECRET_KEY` | 未設定なら `instance/secret_key` を自動生成 |
| `INVENTORY_DATABASE` | SQLiteファイルのパス(既定 `instance/inventory.db`) |
| `INVENTORY_COOKIE_SECURE` | `1` で Cookie に Secure 属性(HTTPS時) |
| `INVENTORY_BEHIND_PROXY` | `1` でプロキシのヘッダを信頼(`tailscale serve` 使用時) |

## 小さいボード(RAM 256〜512MB)で動かすとき

- `gunicorn` は `-w 1 --threads 4` にします(ワーカーを増やすとメモリが増えます)。
- ログイン時のパスワード照合(scrypt)は一時的に約32MBを使います。足りない場合は、
  `generate_password_hash(..., method="pbkdf2:sha256:600000")` に変えると、メモリを使わず時間だけかかります。
- 時計が合わないと Tailscale が繋がらず、履歴の日時も狂います。NTP(chrony など)を自動起動にしてください。
