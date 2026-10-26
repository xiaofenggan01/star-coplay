using System.Collections.Generic;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.Bridge
{
    public interface IProtocolBridge
    {
        List<Envelope> ReadNewProtocolEnvelopes();
        void WriteProtocolResult(string requestId, string sessionId, CommandResult result);
        void WriteProtocolStatus(string requestId, string sessionId, CommandStatusResponse response);
    }
}
