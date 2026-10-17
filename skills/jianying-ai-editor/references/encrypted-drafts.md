# 加密参考草稿

先调用 `get_jianying_capabilities`，检查 `encrypted_draft_reader`：

- `available=true`：可调用 `analyze_reference_draft`、`build_from_reference` 或 `batch_from_template`。
- `available=false`：向用户列出 `reasons`；通常需要配置 `JIANYING_DRAFT_CRYPTO`，或修正 `JIANYING_EXE` 使其所在目录包含 `videoeditor.dll`。

读取器只调用本机剪映组件处理用户有权访问的草稿。流程固定为：复制加密文件到一次性工作目录、解密副本、解析并提取紧凑蓝图、删除临时明文。不得原地解密、回写或覆盖参考草稿。

`source_format` 为 `plaintext_json` 或 `jianying_encrypted`。后者不代表效果资源一定可复现；在线素材仍受剪映版本、账号、会员、缓存和网络影响。若读取器报告导出符号缺失，按版本不兼容处理，不要猜测内容。
