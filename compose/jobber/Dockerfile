FROM jobber

USER root
RUN apk add httpie

COPY jobber.conf /home/jobberuser/.jobber
RUN chown jobberuser /home/jobberuser/.jobber
RUN chmod 600 /home/jobberuser/.jobber
USER jobberuser
