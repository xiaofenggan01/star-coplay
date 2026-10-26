using System.Collections.Generic;
using StarCoPlay.Logging;
using StarCoPlay.Models;

namespace StarCoPlay.Bridge
{
    public interface IBridge
    {
        string BridgeDir { get; }
        void SetLogger(ModLogger logger);
        void EnsureDirectoryExists();
        void Initialize();
        void WriteAdapterInfo(string json);
        void Shutdown();
        void WriteV2Result(string jsonLine);
        void WriteStateAtomic(string json);
        void AppendEvent(string jsonLine);
        int NextSeq();
        List<AgentCommand> ReadNewCommands();
    }
}
