# 取整函数与二进制求和方法库

本文件包括：floor / 取整函数 / 2^k / 2^{k+1} / 无穷求和类题目的标准处理方法、常见错误和本地验证方式。
本文件控制：RAG 检索到同类题时，solver 和 repair 阶段能看到的详细方法模板。
优化时这样操作：以后遇到新的取整求和错题，先补触发词、标准做法、常见错误和可枚举样例；不要把单题完整答案堆成长篇解析。

## floor 与二进制幂求和

触发词：floor、\lfloor、取整、求和、\sum、2^k、2^{k+1}、二进制、数位、popcount。

适用场景：
题目形如对 k 求和，通项含有 $\left\lfloor\frac{n+2^k}{2^{k+1}}\right\rfloor$ 或类似结构，需要化成关于正整数 n 的闭式。

标准做法：
1. 不要直接把 $\lfloor a+1/2\rfloor$ 当成某一位是否为 1；取整函数不能这样口头跳步。
2. 令 $b_k$ 表示 n 的二进制第 k 位，先证明
   $$
   \left\lfloor\frac{n+2^k}{2^{k+1}}\right\rfloor
   =\left\lfloor\frac{n}{2^{k+1}}\right\rfloor+b_k.
   $$
3. 对 k 求和后拆成两部分：
   $$
   \sum_{k\ge0}\left\lfloor\frac{n}{2^{k+1}}\right\rfloor
   +\sum_{k\ge0}b_k.
   $$
4. 使用二进制恒等式
   $$
   \sum_{j\ge1}\left\lfloor\frac{n}{2^j}\right\rfloor=n-s_2(n),
   \quad \sum_{k\ge0}b_k=s_2(n),
   $$
   得到总和为 n。
5. 最后用小样例表核验 n=1,2,3,4,5,8,15。

常见错误：
- 把最终答案误判成 n 的二进制表示中 1 的个数。
- verifier 把 candidate 与 expected 相同的样例当成反例。
- 只列样例就给答案，没有证明高位贡献与数位和如何抵消。

本地验证建议：
- 对候选公式直接计算 n=1,2,3,4,5,8,15 的有限非零项。
- 如果候选为 $S=n$ 且样例表全部匹配，可用于仲裁 verifier 的明显自相矛盾。
- 如果候选为 popcount(n)，n=2 或 n=4 会立刻反驳。
