# 参与 First Principles Pilot

欢迎提交缺陷修复、文档改进和功能建议。改动较大时，先开 Issue 说明使用场景、预期行为及影响范围。

## 报告问题

请提供软件版本、操作系统和架构、复现步骤、预期与实际行为，以及相关错误日志。输入文件尽量简化为最小样例。上传前移除账号、主机地址、密钥、私人脚本和研究数据；不要上传 POTCAR。科学计算问题还应说明单位、实际输入、程序版本和收敛情况。

## 开发

1. Fork 仓库并从当前 `main` 创建功能分支。
2. 按 [README](README.md#从源码运行) 准备 Node.js 和 Python 运行依赖。
3. 保持 Pull Request 聚焦于一个问题；说明触发条件、行为变化和验证结果。
4. 扩展的实现与独立分发在 `extensions` 分支进行；宿主协议改动请单独说明兼容性。

## 验证

```sh
npm test
npm run build
python -m unittest tests.test_extension_host tests.test_remote_paths
```

按改动选择相关 Python 模块；完整发现命令为 `python -m unittest discover -s tests -p 'test_*.py'`。部分历史用例依赖开发机登记表、私有样例或可选环境，干净克隆可能报告相应失败或跳过。请如实记录并对照未修改版本，不要为测试上传私人夹具。

使用合成数据、临时工作区和模拟 SSH 验证运行逻辑。提交远端任务、删除研究文件或改变真实工作区不属于普通测试。计算完成、数值收敛与物理有效性需要分别说明。

## Pull Request

描述问题与修复后的行为，列出实际执行的检查和未覆盖的边界。涉及输入来源、`UNKNOWN` 恢复、路径冻结或数据清理时，说明对历史记录的影响。保留第三方版权与许可证；项目原创代码按 [MIT License](LICENSE) 提交。
