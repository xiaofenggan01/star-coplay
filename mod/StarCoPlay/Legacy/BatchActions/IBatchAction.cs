namespace StarCoPlay.Actions
{
    /// <summary>
    /// 批量操作统一接口。
    /// 每个实现是一个非阻塞状态机：Start() 初始化，Tick() 每帧推进一步，Cancel() 中止。
    /// </summary>
    public interface IBatchAction
    {
        /// <summary>操作是否已完成（成功或失败）。</summary>
        bool IsComplete { get; }

        /// <summary>操作是否成功完成。仅在 IsComplete 为 true 时有意义。</summary>
        bool IsSuccess { get; }

        /// <summary>操作结果描述（完成/失败原因/进度摘要）。</summary>
        string ResultMessage { get; }

        /// <summary>错误码（仅失败时有值）。</summary>
        string? ErrorCode { get; }

        /// <summary>初始化操作：扫描目标、装备工具等。</summary>
        void Start();

        /// <summary>每帧推进一步（非阻塞，不能 while 循环）。</summary>
        void Tick();

        /// <summary>取消当前操作。</summary>
        void Cancel();
    }
}
